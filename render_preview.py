#!/usr/bin/env python3
"""Render the exact CPU-equivalent skin deformation into a two-view MP4 preview.

Optional visual-review dependencies: pip install trimesh vtk pillow
Also requires ffmpeg in PATH. GLB rig/export itself needs only NumPy.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np
import trimesh
import vtk
from vtk.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy
from PIL import Image, ImageDraw, ImageFont

from fish_swim import PRESETS, make_rig, skin_vertices


def _texture(material, limit: int = 1536) -> vtk.vtkTexture:
    img = material.baseColorTexture.copy()
    img.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    img = img.convert('RGB')
    array = np.flipud(np.asarray(img)).copy()
    scalars = numpy_to_vtk(array.reshape(-1, 3), deep=True,
                           array_type=vtk.VTK_UNSIGNED_CHAR)
    scalars.SetNumberOfComponents(3)
    data = vtk.vtkImageData()
    data.SetDimensions(img.width, img.height, 1)
    data.GetPointData().SetScalars(scalars)
    texture = vtk.vtkTexture()
    texture.SetInputData(data)
    texture.InterpolateOn()
    return texture


def _scene(mesh: trimesh.Trimesh, width: int, height: int):
    poly = vtk.vtkPolyData()
    points = vtk.vtkPoints()
    points.SetData(numpy_to_vtk(mesh.vertices.astype('float32'), deep=True))
    poly.SetPoints(points)
    faces = np.c_[np.full(len(mesh.faces), 3, dtype='int64'),mesh.faces.astype('int64')].ravel()
    cells = vtk.vtkCellArray()
    cells.SetCells(len(mesh.faces),numpy_to_vtkIdTypeArray(faces, deep=True))
    poly.SetPolys(cells)
    uv = numpy_to_vtk(np.asarray(mesh.visual.uv,dtype='float32'), deep=True)
    uv.SetNumberOfComponents(2)
    poly.GetPointData().SetTCoords(uv)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(poly)
    texture = _texture(mesh.visual.material)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(width,height)
    for label,viewport,bg,camera_xyz,up in (
        ('lateral',(0,.5,1,1),(.041,.054,.074),(-4.4,0,0),(0,1,0)),
        ('dorsal',(0,0,1,.5),(.032,.046,.068),(0,4.4,0),(1,0,0)),
    ):
        actor=vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetTexture(texture)
        actor.GetProperty().SetAmbient(.52)
        actor.GetProperty().SetDiffuse(.55)
        actor.GetProperty().SetSpecular(.03)
        actor.GetProperty().SetInterpolationToPhong()
        renderer=vtk.vtkRenderer()
        renderer.SetBackground(*bg)
        renderer.SetViewport(*viewport)
        renderer.AddActor(actor)
        cam=renderer.GetActiveCamera()
        cam.SetPosition(*camera_xyz)
        cam.SetFocalPoint(0,0,0)
        cam.SetViewUp(*up)
        cam.ParallelProjectionOn()
        cam.SetParallelScale(.365)
        window.AddRenderer(renderer)
    window.Render()
    capture=vtk.vtkWindowToImageFilter()
    capture.SetInput(window)
    capture.ReadFrontBufferOff()
    capture.Update()
    return window,points,capture


def _render_frame(window,points,capture,vertices,rig,t,preset,width,height) -> Image.Image:
    new_vertices=skin_vertices(vertices,rig,t,preset)
    vtk_to_numpy(points.GetData())[:]=new_vertices
    points.GetData().Modified()
    points.Modified()
    window.Render()
    capture.Modified();capture.Update()
    img=capture.GetOutput()
    pix=vtk_to_numpy(img.GetPointData().GetScalars())
    nchan=img.GetNumberOfScalarComponents()
    arr=np.flipud(pix.reshape(height,width,nchan)[:,:,:3]).copy()
    return Image.fromarray(arr)


def _decorate(img:Image.Image,preset_name:str,time_s:float,duration_s:float) -> Image.Image:
    dr=ImageDraw.Draw(img,'RGBA')
    w,h=img.size
    try:
        font=ImageFont.truetype('DejaVuSans.ttf',19)
        small=ImageFont.truetype('DejaVuSans.ttf',13)
    except OSError:
        font=ImageFont.load_default();small=font
    dr.rounded_rectangle((16,12,242,49),radius=11,fill=(10,19,31,198),outline=(128,153,170,80),width=1)
    dr.text((30,19),'LATERAL  /  SIDE',fill=(239,245,253,255),font=font)
    mid=h//2
    dr.line([(0,mid),(w,mid)],fill=(87,114,140,155),width=2)
    dr.rounded_rectangle((16,mid+12,265,mid+49),radius=11,fill=(10,19,31,198),outline=(128,153,170,80),width=1)
    dr.text((30,mid+19),'DORSAL  /  TOP',fill=(239,245,253,255),font=font)
    dr.rounded_rectangle((w-325,12,w-18,47),radius=9,fill=(10,19,31,180))
    dr.text((w-310,20),f'{preset_name.replace("_"," ")}    {time_s:.2f}s',font=small,fill=(223,235,248,255))
    dr.rectangle((18,h-15,w-18,h-11),fill=(78,94,111,120))
    dr.rectangle((18,h-15,18+int((w-36)*(time_s/duration_s)),h-11),fill=(100,199,230,255))
    return img


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',type=Path,help='Original source mesh (not the skinned GLB)')
    ap.add_argument('output',type=Path,help='Destination MP4')
    ap.add_argument('--clip',choices=[p.name for p in PRESETS],default='Cruise_2Hz')
    ap.add_argument('--fps',type=int,default=24)
    ap.add_argument('--duration',type=float,default=2.0)
    ap.add_argument('--width',type=int,default=1080)
    ap.add_argument('--height',type=int,default=700)
    ap.add_argument('--poster',type=Path,default=None,help='Optional 4-phase contact sheet image')
    args=ap.parse_args()
    if shutil.which('ffmpeg') is None:
        raise SystemExit('ffmpeg not found')
    if args.height % 2 or args.width % 2:
        ap.error('width and height must be even for video encoding')
    mesh=trimesh.load(args.source,force='mesh',process=False)
    vertices=np.asarray(mesh.vertices,dtype='float32')
    rig=make_rig(vertices)
    preset=next(p for p in PRESETS if p.name==args.clip)
    win,pts,cap=_scene(mesh,args.width,args.height)
    posters=[]
    with tempfile.TemporaryDirectory(prefix='swim_frames_') as folder:
        times=np.linspace(0,args.duration,round(args.fps*args.duration)+1)
        for i,t in enumerate(times):
            img=_render_frame(win,pts,cap,vertices,rig,float(t),preset,args.width,args.height)
            img=_decorate(img,preset.name,float(t),args.duration)
            img.save(f'{folder}/frame_{i:04d}.png',compress_level=1)
            if i in (0,round(args.fps*.125),round(args.fps*.25),round(args.fps*.375)):
                posters.append(img.crop((0,args.height//2,args.width,args.height)))
            if i % 12 == 0:
                print('Rendered frame',i,'/',len(times)-1,flush=True)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        cmd=['ffmpeg','-hide_banner','-loglevel','error','-y',
             '-framerate',str(args.fps),'-i',f'{folder}/frame_%04d.png',
             '-c:v','libx264','-preset','medium','-crf','21',
             '-pix_fmt','yuv420p','-movflags','+faststart',str(args.output)]
        subprocess.run(cmd,check=True)
    if args.poster and len(posters)==4:
        display_w=600
        ratio=display_w/args.width
        thumbs=[p.resize((display_w,round(p.height*ratio)),Image.Resampling.LANCZOS) for p in posters]
        sheet=Image.new('RGB',(display_w*2,thumbs[0].height*2),(18,28,42))
        for i,img in enumerate(thumbs): sheet.paste(img,((i%2)*display_w,(i//2)*thumbs[0].height))
        args.poster.parent.mkdir(parents=True,exist_ok=True)
        sheet.save(args.poster,optimize=True)
    print('Wrote',args.output,flush=True)

if __name__=='__main__':main()
