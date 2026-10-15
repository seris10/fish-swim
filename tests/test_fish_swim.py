"""Deterministic tests of procedural rig, skin transforms and GLB serialization."""
from pathlib import Path
import hashlib
import json
import math
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import fish_swim as f


def synthetic_mesh():
    zs=np.linspace(-1.,1.,91)
    theta=np.linspace(0.,2*np.pi,12,endpoint=False)
    return np.asarray([[.055*np.cos(t),.11*np.sin(t),z] for z in zs for t in theta],dtype=np.float32)


class SwimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.points=synthetic_mesh()
        cls.rig=f.make_rig(cls.points)

    def test_weights_are_valid_and_deterministic(self):
        w=self.rig.weights
        self.assertTrue(np.all(np.isfinite(w)))
        self.assertTrue(np.all(w>=0.))
        self.assertTrue(np.allclose(w.sum(1),1.,atol=2e-7))
        self.assertTrue(np.all(self.rig.joints < len(self.rig.joint_z)))
        other=f.make_rig(self.points)
        np.testing.assert_array_equal(other.weights,w)

    def test_invalid_vertices_are_rejected(self):
        for points in (np.zeros((0, 3)), np.zeros((5, 3)), np.full((5, 3), np.nan)):
            with self.subTest(shape=points.shape), self.assertRaises(ValueError):
                f.make_rig(points)

    def test_unapplied_transforms_are_rejected_before_export(self):
        for field, value in [('scale', [2, 2, 2]), ('translation', [1, 0, 0]), ('rotation', [0, 1, 0, 0])]:
            doc = {'nodes': [{'mesh': 0, field: value}],
                   'meshes': [{'primitives': [{'attributes': {}}]}]}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'transform'):
                f.build_animated_glb(doc, b'', self.rig)

    def test_source_mesh_with_parent_is_rejected(self):
        doc = {'nodes': [{'mesh': 0}, {'children': [0]}],
               'meshes': [{'primitives': [{'attributes': {}}]}]}
        with self.assertRaisesRegex(ValueError, 'parent'):
            f.build_animated_glb(doc, b'', self.rig)

    def test_cli_refuses_output_hard_link_to_input(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'input.glb'
            source.write_bytes(b'original bytes')
            output = Path(folder) / 'output.glb'
            output.hardlink_to(source)
            with patch('sys.argv', ['fish-swim', str(source), str(output)]), self.assertRaises(SystemExit) as exc:
                f.main()
            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(source.read_bytes(), b'original bytes')

    def test_rest_skin_is_identity(self):
        out=f.skin_vertices(self.points,self.rig,0.,f.PRESETS[1],amplitude_multiplier=0.)
        np.testing.assert_allclose(out,self.points,atol=1e-6)

    def test_head_still_and_tail_moves(self):
        p=f.PRESETS[1]
        a=f.skin_vertices(self.points,self.rig,0.,p)
        b=f.skin_vertices(self.points,self.rig,.125,p)
        head=self.points[:,2]>.81
        tail=self.points[:,2]<-.8
        self.assertTrue(np.max(np.abs(a[head]-self.points[head]))<1e-4)
        self.assertTrue(np.mean(np.abs(b[tail,0]-a[tail,0]))>.005)

    def test_loop_is_closed(self):
        for profile in f.PRESETS:
            np.testing.assert_allclose(f.local_spine_angles(0.,self.rig,profile),
                                       f.local_spine_angles(2.,self.rig,profile),atol=1e-12)
            q=f.quaternions_y(f.local_spine_angles(.15,self.rig,profile))
            np.testing.assert_allclose(np.linalg.norm(q,axis=1),1.0,atol=1e-6)

    def test_rest_inverse_bind(self):
        matrices=f.joint_world_matrices(self.rig,np.zeros(len(self.rig.joint_z)))
        for m in matrices:
            np.testing.assert_allclose(m@np.linalg.inv(m),np.eye(4),atol=1e-12)

    def test_one_glb_binary_round_trip_and_hash(self):
        pts=self.points[::12]
        # Small standalone glTF document in memory, sharing the same component/accessor logic.
        d={'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],
           'nodes':[{'mesh':0,'matrix':[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]}], 'meshes':[{'primitives':[{'attributes':{},'indices':0}]}],
           'buffers':[{'byteLength':0}], 'bufferViews':[], 'accessors':[]}
        a=f.Appender(d,b'')
        idx=a.add(np.arange(0,21,dtype='uint32').reshape(-1,1),'SCALAR',5125,target=34963)
        pos=a.add(pts,'VEC3',5126,target=34962)
        d['meshes'][0]['primitives'][0]['indices']=idx
        d['meshes'][0]['primitives'][0]['attributes']['POSITION']=pos
        d['buffers'][0]['byteLength']=len(a.blob)
        with tempfile.TemporaryDirectory() as folder:
            infile=Path(folder)/'original.glb';outfile=Path(folder)/'animated.glb'
            f.write_glb(infile,d,bytes(a.blob))
            sha=hashlib.sha256(infile.read_bytes()).hexdigest()
            doc,bb=f.read_glb(infile)
            rig=f.make_rig(f.accessor_array(doc,bb,pos),num_joints=6)
            od,ob=f.build_animated_glb(doc,bb,rig,source_hash=sha)
            f.write_glb(outfile,od,ob)
            parsed,packed=f.read_glb(outfile)
            self.assertEqual(len(parsed['animations']),3)
            self.assertEqual(len(parsed['skins']),1)
            self.assertEqual(len(parsed['nodes']),8)
            self.assertEqual(hashlib.sha256(infile.read_bytes()).hexdigest(),sha)
            self.assertTrue(packed.startswith(bb))
            tri_pos=f.accessor_array(parsed,packed,parsed['meshes'][0]['primitives'][0]['attributes']['POSITION'])
            np.testing.assert_array_equal(tri_pos,pts)
            self.assertNotIn('matrix',parsed['nodes'][0], 'glTF skinned nodes require TRS, not matrix')
            self.assertEqual(len(parsed['skins'][0]['joints']),6)
            self.assertTrue(all(len(anim['channels'])==6 for anim in parsed['animations']))


if __name__=='__main__':unittest.main()
