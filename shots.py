"""LTX 分镜提示词（18 镜 / 四幕 / 约 61 秒）。

原定义在 run_ltx23_multishot.py 内；该运行器已归档到 legacy/，
故抽出为独立模块，供 run_ltx25_multishot.py 与 webui.py 复用。
"""

# ---- 分镜：一场连续的戏（雨夜赛博都市，主角穿行、记忆闪现、走向发光门）----
# 主角一致性锚点：每个分镜都带上，尽量让 18 个独立 T2V 镜头里的人物外观稳定
HERO = ("a lone protagonist in a long dark coat, short dark hair, "
        "a faint glowing scar along the left cheek")
# 项目统一视觉风格（取自 config.yaml project.style）
STYLE = "anime style, cel-shaded, clean line art, vibrant colors, dramatic lighting"

# ---- 完整短片分镜：18 镜 / 四幕 / 约 61 秒 ----
SHOTS = [
    # ===== 第一幕 · 开端：建立世界与主角 =====
    # 1 全景 · 赛博都市
    "A vast rain-slicked neon street in a cyberpunk metropolis at night, towering "
    "holographic billboards in cyan and magenta, crowds with augmented-reality overlays, "
    "flying vehicles above, a lone figure in a long dark coat walking in the far distance, "
    f"{STYLE}, slow camera movement",
    # 2 中景 · 主角登场
    f"{HERO}, walking down a narrow neon alley, rain falling through volumetric light, "
    f"holographic advertisements flickering overhead, {STYLE}, slow tracking shot",
    # 3 特写 · 记忆母题
    "Extreme close-up of a gloved hand opening, a cluster of glowing memory fragments "
    "floating above the palm like tiny holographic shards, rain in the background, "
    f"{STYLE}, shallow depth of field",
    # 4 全景 · 城市
    "Wide shot of the cyberpunk skyline at night, flying vehicles streaming between "
    "megatowers, giant holographic advertisements reflecting in rain clouds, cyan and "
    f"magenta neon, {STYLE}, slow camera pan",
    # 5 中景 · 进入记忆诊所
    f"{HERO}, pushing open the door of a small memory clinic, warm amber light spilling "
    f"onto the wet street, a faded neon sign above, {STYLE}, slow camera movement",

    # ===== 第二幕 · 发展：记忆交易与身份危机 =====
    # 6 内景 · 记忆扫描
    "Interior of a dim memory clinic, the protagonist seated in a chrome chair, a ring of "
    "blue scanning light sweeping across the face, cables and old monitors around, "
    f"{STYLE}, slow camera push in",
    # 7 特写 · 身份数据
    "Close-up of a cracked monitor displaying cascading streams of the protagonist's memory "
    "data, green and amber code, a silhouette of a human profile formed of flowing particles, "
    f"{STYLE}",
    # 8 中景 · 交易筹码
    "A masked memory dealer in a dark clinic interior extending a hand holding a small "
    "glowing memory chip, amber backlight, the protagonist's face half-lit in the foreground, "
    f"{STYLE}, shallow depth of field",
    # 9 特写 · 犹豫
    "Close-up of the protagonist's face, eyes wide with hesitation, reflections of flowing "
    "data streams in the wet eyes, cyan and amber light, mysterious and emotional, "
    f"{STYLE}, shallow depth of field",
    # 10 主观 · 看见未来（点题）
    "The protagonist's point of view, a vision of an older version of himself standing in "
    "the rain across the street, translucent and flickering like a hologram, neon "
    f"reflections, {STYLE}, slow camera push forward",
    # 11 闪回 · 失落的记忆
    "A warm memory flashback, a young child laughing in a sunlit field of tall grass, "
    f"golden hour light, soft bokeh, nostalgic and tender, {STYLE}, slow camera movement",
    # 12 特写 · 门（母题）
    "Close-up of an old wooden door glowing faintly at its edges, standing alone in a dark "
    f"void filled with drifting memory fragments, hope and mystery, {STYLE}, slow camera push in",

    # ===== 第三幕 · 高潮：抉择与对抗 =====
    # 13 中景 · 逃离
    f"{HERO}, bursting out of the memory clinic into the rain-soaked neon street, coat "
    f"flaring, running away from camera, reflections scattering in puddles, {STYLE}, "
    "fast tracking shot",
    # 14 全景 · 系统苏醒
    "Wide shot of the city's surveillance system awakening, security drones rising from "
    "megatowers, red scanning lights sweeping through the rain, the tiny figure of the "
    f"protagonist far below, {STYLE}, slow crane shot",
    # 15 特写 · 捏碎芯片（选择保留自我）
    "Extreme close-up of a hand crushing the glowing memory chip, sparks and shards of light "
    f"falling like rain, determined, cyan and amber light, {STYLE}, shallow depth of field",
    # 16 中景 · 对峙
    f"{HERO}, standing in the rain looking up, surrounded by hovering security drones with "
    f"red scanning beams, coat whipping in the wind, defiant, {STYLE}, low angle hero shot",

    # ===== 第四幕 · 结尾：主题收束 =====
    # 17 主观 · 光之门
    "The protagonist's point of view, a distant glowing gate of light appearing through rain "
    "and fog at the end of the street, holographic signs and flying vehicles in the sky, "
    f"{STYLE}, slow camera push forward",
    # 18 全景 · 走向光门，拉远
    "The lone figure in a long dark coat walking toward the glowing gate down the "
    "rain-slicked street, the vast cyberpunk skyline fading into fog behind, camera slowly "
    f"pulling back, hope and mystery, {STYLE}",
]
