extends Node3D
## Attach to the imported fish scene root or a parent node.
## The GLB faces +Z. Motion speed/orientation is handled by your game simulation.
## Clips represent a kinematic approximation, not measured trout behavior.

@export_enum("hold", "cruise", "burst") var initial_state: String = "cruise"
@export_range(0.1, 3.0, 0.05) var rate_multiplier: float = 1.0

var _player: AnimationPlayer


func _ready() -> void:
    _player = _find_player(self)
    if _player == null:
        push_warning("Fish GLB has no AnimationPlayer. Check glTF animation import.")
        return
    # glTF does not dictate automatic loop playback, so enable it in Godot.
    for animation_name in _player.get_animation_list():
        var clip: Animation = _player.get_animation(animation_name)
        if clip != null:
            clip.loop_mode = Animation.LOOP_LINEAR
    set_swim_state(initial_state)


func set_swim_state(state: String) -> void:
    if _player == null:
        return
    var label := "Cruise_2Hz"
    match state:
        "hold": label = "Hold_1Hz"
        "cruise": label = "Cruise_2Hz"
        "burst": label = "Burst_3Hz"
        _: push_warning("Unknown fish swim state: " + state)
    var clip_name := _resolve_clip(label)
    if clip_name.is_empty():
        push_warning("Missing animation clip: " + label)
        return
    _player.play(clip_name, 0.2)
    _player.speed_scale = rate_multiplier


func set_playback_rate(rate: float) -> void:
    rate_multiplier = clampf(rate, 0.1, 3.0)
    if _player != null:
        _player.speed_scale = rate_multiplier


func _resolve_clip(suffix: String) -> StringName:
    for animation_name in _player.get_animation_list():
        if String(animation_name) == suffix or String(animation_name).ends_with("/" + suffix):
            return animation_name
    return &""


func _find_player(root: Node) -> AnimationPlayer:
    if root is AnimationPlayer:
        return root
    for child in root.get_children():
        var result := _find_player(child)
        if result != null:
            return result
    return null
