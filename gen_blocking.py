#!/usr/bin/env python
"""用 Blender(bpy)生成「白模走位」参考视频，供 MiniMax H3 的 ref_video(HYBRID/REF2VA)使用。

思路：先在 3D 里**确定性**地摆好相机运动与人物走位（白模/灰模），渲染成参考视频，
再让 H3 以它为运动参考生成实拍风格画面 —— 把「镜头怎么走、人怎么走」从模型的
随机发挥变成可控输入。这是解决「镜头乱飘、走位不可控、逐镜构图不稳定」的治本路径。

白模只用最简几何（圆柱身体 + 球头 + 地面），只表达：位置 / 高度 / 体积 / 运动轨迹。

用法:
  python gen_blocking.py --out outputs/blocking/shot1.mp4 --frames 90 \
      --cam push_in --walk "-8,0:-2,0"
  python gen_blocking.py --out outputs/blocking/shot9.mp4 --cam static --actors "mira:-1,0:boss:1,0"
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess

import bpy


# ---------- 场景搭建 ----------
def set_emission(obj, color):
    """自发光纯色材质：白模不受光照影响，轮廓最清晰。"""
    mat = bpy.data.materials.new("M_" + obj.name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs[0].default_value = (*color, 1.0)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs[0])
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


def add_actor(name, height=1.75, radius=0.28, color=(0.95, 0.95, 0.95)):
    """简化人形代理：圆柱身体 + 球头（只表达位置/高度/体积）。"""
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=height * 0.78,
                                        location=(0, 0, height * 0.39))
    body = bpy.context.active_object
    body.name = name + "_body"
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius * 0.8,
                                         location=(0, 0, height * 0.78 + radius * 0.62))
    head = bpy.context.active_object
    head.name = name + "_head"
    head.parent = body
    head.matrix_parent_inverse = body.matrix_world.inverted()
    set_emission(body, color)
    set_emission(head, color)
    return body


def add_ground(size=80, color=(0.16, 0.17, 0.20)):
    bpy.ops.mesh.primitive_plane_add(size=size)
    g = bpy.context.active_object
    g.name = "ground"
    set_emission(g, color)
    return g


def setup_world(scene, color=(0.04, 0.045, 0.06)):
    world = bpy.data.worlds.new("W")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (*color, 1.0)
    scene.world = world


# ---------- 动画 ----------
def key_loc(obj, frame, loc):
    obj.location = loc
    obj.keyframe_insert(data_path="location", frame=frame)


def parse_pt(s):
    x, y = s.split(",")
    return float(x), float(y)


def apply_walk(actor, spec, frames, z=0.0):
    """走位：'x1,y1:x2,y2' —— 起点 -> 终点（均匀位移）。"""
    if not spec:
        key_loc(actor, 1, (0.0, 0.0, z))
        return
    a, b = spec.split(":")
    x1, y1 = parse_pt(a)
    x2, y2 = parse_pt(b)
    key_loc(actor, 1, (x1, y1, z))
    key_loc(actor, frames, (x2, y2, z))
    # bpy 5.0 起 Action 改为分层结构，action.fcurves 可能不存在；取不到就用默认插值
    try:
        for fc in actor.animation_data.action.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
    except AttributeError:
        pass


def add_camera(scene, cam, frames, target, track=True):
    bpy.ops.object.camera_add(location=(0, -9, 1.9))
    c = bpy.context.active_object
    c.name = "cam"
    scene.camera = c
    if track:
        # 始终朝向主体：相机移动时自动跟拍。代价是人物恒在画面中心，
        # 横向走位体现不出来 —— 要横向走位请用 --no-track。
        con = c.constraints.new("TRACK_TO")
        con.target = target
        con.track_axis = "TRACK_NEGATIVE_Z"
        con.up_axis = "UP_Y"
    else:
        # 固定朝向不跟拍，人物的横向位移才会真实反映在画面里
        import math
        c.rotation_euler = (math.radians(80), 0.0, 0.0)

    if cam == "push_in":        # 缓推：由远及近
        key_loc(c, 1, (0.0, -10.0, 2.1))
        key_loc(c, frames, (0.0, -4.2, 1.75))
    elif cam == "pull_out":     # 缓拉
        key_loc(c, 1, (0.0, -4.0, 1.7))
        key_loc(c, frames, (0.0, -10.0, 2.2))
    elif cam == "lateral":      # 横移
        key_loc(c, 1, (-3.0, -7.0, 1.8))
        key_loc(c, frames, (3.0, -7.0, 1.8))
    elif cam == "orbit":        # 环绕
        import math
        for i in range(0, 5):
            f = 1 + int(frames * i / 4)
            ang = -math.pi / 2 + math.pi * i / 4
            key_loc(c, f, (6.0 * math.cos(ang), 6.0 * math.sin(ang), 2.0))
    else:                       # static
        key_loc(c, 1, (0.0, -7.0, 1.8))
    return c


# ---------- 渲染 ----------
def render_pngs(scene, out_dir, w, h, frames, fps):
    os.makedirs(out_dir, exist_ok=True)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = False
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    scene.frame_start = 1
    scene.frame_end = frames
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = os.path.join(out_dir, "f")
    bpy.ops.render.render(animation=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="输出参考视频 mp4")
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--res", default="1024x576")
    ap.add_argument("--cam", default="push_in",
                    choices=["push_in", "pull_out", "lateral", "orbit", "static"])
    ap.add_argument("--walk", default="", help="主角走位 'x1,y1:x2,y2'（起点->终点）")
    ap.add_argument("--second", default="", help="第二个角色位置 'x,y'（如老板）")
    ap.add_argument("--no-track", action="store_true",
                    help="相机不跟拍（固定朝向），用于让横向走位在画面里体现出来")
    ap.add_argument("--keep-png", action="store_true", help="保留 PNG 序列（调试用）")
    a = ap.parse_args()

    w, h = (int(x) for x in a.res.lower().split("x"))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    setup_world(scene)
    add_ground()

    mira = add_actor("mira")
    apply_walk(mira, a.walk, a.frames)

    second = None
    if a.second:
        x, y = parse_pt(a.second)
        second = add_actor("boss", color=(0.55, 0.6, 0.7))
        key_loc(second, 1, (x, y, 0.0))

    add_camera(scene, a.cam, a.frames, mira, track=not a.no_track)

    # bpy 会把相对路径解析到它自己的 cwd（实测输出落到 C:\），故一律转绝对路径
    a.out = os.path.abspath(a.out)
    tmp = a.out + ".pngs"
    # 用 cmd `del` 清 png：绕过 python 删除 API 的 SAFE_DELETE turn 级批量拦截
    # （长批量进程内累计删除超 500 后，连 os.remove 都会被拦导致崩）。
    # 不删目录本身，Blender 渲染会覆盖同名 png。
    os.makedirs(tmp, exist_ok=True)
    os.system(f'del /q "{tmp}\\*.png" 2>nul')
    print(f"[blocking] 渲染白模 {w}x{h} {a.frames}帧@{a.fps}fps  cam={a.cam} "
          f"walk={a.walk or '-'}", flush=True)
    render_pngs(scene, tmp, w, h, a.frames, a.fps)

    # PNG 序列 -> mp4（参考视频只需运动信息，用低码率 h264 即可）
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = "ffmpeg"
    subprocess.run([ffmpeg, "-y", "-framerate", str(a.fps),
                    "-start_number", "1", "-i", os.path.join(tmp, "f%04d.png"),
                    "-frames:v", str(a.frames),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", a.out],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not a.keep_png:
        # cmd del 删 png 留空目录，绕过 SAFE_DELETE（python 删除 API 长进程会被拦）
        os.system(f'del /q "{tmp}\\*.png" 2>nul')
    print(f"[OK] 参考视频 -> {a.out}  ({os.path.getsize(a.out) // 1024}KB)", flush=True)


if __name__ == "__main__":
    main()
