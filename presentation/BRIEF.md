# Presentation animations — brief

用于一次 presentation：向**不懂 Maya、不懂 3D 流程**的听众讲解 MayaMixamoAssist 插件的工作原理。
需要做 4 段简短的动态插图，在浏览器里直接打开播放。

## 已确认的要求

| 项目 | 决定 |
|---|---|
| 格式 | 网页（HTML），演讲时在浏览器全屏播放 |
| 播放 | 打开即自动播放、自动循环；每段 6–12 秒 |
| 风格 | 极简线条：骨骼 = 线段 + 圆点关节，少量高亮色，少量标注 |
| 背景 | 深色 |
| 画面文字 | 英文，短句，像字幕一样随步骤切换 |
| 位置 | `D:\Projects\MayaMixamoAssist\presentation\` |

## 技术约束

- 每段动画一个独立的 HTML 文件，外加一个 `index.html` 列出 4 段并可点击打开。
- **完全离线可用**：只用原生 HTML + 内联 SVG + 原生 JS/CSS，不引用 CDN 或外部字体（演讲现场可能没网）。
- 固定 16:9 逻辑画布（1920×1080），按窗口等比缩放居中，不出现滚动条。
- 投影可读性：1080p 下正文字号 ≥ 28px，标题 ≥ 44px；线条足够粗（≥ 4px）。
- 每个画面同时出现的元素尽量少；动作慢一点，让人看清；步骤之间留停顿。
- 配色：深灰近黑背景；骨骼浅灰；每段只用 1–2 个高亮色。全套统一一套色板，比如 root = 橙色，IK 控制器 = 蓝色，修改后的部分 = 青色。
- 做完用浏览器打开逐个检查（截图或录屏），确认动画、文字、缩放都正确。

## 听众需要明白的背景（用大白话）

- **Mixamo**：免费网站，提供现成的人物动作（走、跑、跳…）。
- **Maya**：3D 动画软件。插件装在 Maya 里。
- **Unreal Engine (UE)**：游戏引擎，动画最终在这里用。
- 角色动画 = 一副“骨架”每一帧摆出的姿势；模型跟着骨架动。
- 问题：Mixamo 的动作直接进游戏不好用，需要在 Maya 里处理一下。插件把这些处理变成一键操作。

## 4 段动画的分镜（侧视图，2D）

### 1. `pipeline.html` — From Mixamo to Unreal（约 10 秒）
三个方框从左到右：**Mixamo → Maya + Plugin → Unreal Engine**，用箭头连接。
1. 左框：一个火柴人骨架在原地做走路动作。字幕：*Download an animation from Mixamo*
2. 骨架沿箭头移到中框：脚下出现一个橙色 root 标记，手的位置被微调一下。字幕：*The plugin prepares it in Maya*
3. 骨架移到右框：在地面上向前走，身后有一个简单的“游戏角色胶囊体”跟着走。字幕：*Ready to use in the game*

### 2. `root.html` — Root & Root Motion（约 12 秒）
侧视图，一条地面线，原点处有一个小十字标记。
1. 骨架向前走，骨盆（髋部，用一个高亮点表示）上下起伏、往前移动。字幕：*Mixamo: the hips carry all the movement*
2. 原点地面上出现一个橙色菱形 = root。字幕：*Add Root: a "root" point on the ground*
3. 再播放走路：root 贴着地面、跟在骨盆正下方向前滑；骨盆和 root 之间有一条竖向虚线；root **不会**跟着上下起伏。字幕：*Follow Root: the root follows the hips, staying on the ground*
4. 快速补充一幕：一个起点不在原点的骨架整体平移，让第一帧骨盆正好落在原点上方，高度不变。字幕：*Move Pelvis to Origin: every animation starts at the same spot*
5. 结尾一句：*The game engine reads the root to move the character*

### 3. `fk_ik.html` — FK vs IK（约 8 秒）
左右分屏，各一条两节手臂（肩 → 肘 → 手），画面里有一个星形目标点。
- 左边 **FK**：先转肩膀（关节旁边画一段旋转弧线），再转手肘，一节一节把手转到星星上。字幕：*FK: rotate each joint, one by one*
- 右边 **IK**：一个鼠标光标直接把手拖到星星上，整条手臂自动跟着弯曲。手肘旁边有一个小的方向标记（pole），表示手肘朝哪边弯。字幕：*IK: move the hand — the arm follows*
- 两边可以先后播放，也可以同时播放，以清楚为准。

### 4. `range_edit.html` — Edit Just a Part（约 12 秒）
上半部分：一条时间轴（0–100 帧）。下半部分：一条手臂在做一个简单的循环动作，比如挥手。
1. 时间轴上高亮一段（比如 40–60），两边各有一段渐变（淡入、淡出）。字幕：*Pick a range of frames*
2. 时间轴上方画出权重曲线：0 → 平滑升到 1 → 保持 → 平滑降回 0。字幕：*Pose the hand once*
3. 播放头沿时间轴移动。手的原始轨迹用灰色虚线表示，修改后的轨迹用青色；只有播放头在高亮段时，手才偏离灰色轨迹，并且在两边平滑地回到原轨迹。字幕：*It blends in and out smoothly*
4. 一个 “Layer ON / OFF” 开关切换两次，演示关掉就是原动画。字幕：*Outside the range — the original animation is untouched*

## 插件实际做了什么（保证示意图不画错）

- **Add Root Joint**：在地面原点、髋部骨骼上方加一个 root 骨骼，不改动画。
- **Add Follow Root**：root 每帧跟随骨盆在地面上的投影，默认只跟随前后和左右方向，高度保持在地面；骨盆的世界运动完全不变。
- **Move Pelvis to Origin**：整段动画统一平移，让第一帧骨盆在 (0, 原高度, 0)。
- **Limb IK**：给手和脚加临时 IK 控制器；不做修改时和原动画完全一致。手肘和膝盖的朝向有单独的控制点（pole）。
- **Edit Layer**：在一个时间段内叠加修改，权重曲线负责淡入淡出；可以静音对比；最后烘焙回骨骼，只烘焙改过的那段，其他帧保持原样。
- 最后导出给 UE。在 UE 里由 root 驱动角色移动（Root Motion）。

## 不确定时

先问用户，不要自己猜。用户用中文交流。
