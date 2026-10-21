# -*- coding: utf-8 -*-
"""
Headless tests for mixamo_root_tool. Run with mayapy:

    mayapy tests/test_mixamo_root_tool.py
"""
import importlib.util
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")

import maya.cmds as cmds  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_PATH = os.path.normpath(os.path.join(HERE, "..", "plug-ins", "mixamo_root_tool.py"))

spec = importlib.util.spec_from_file_location("mixamo_root_tool_under_test", PLUGIN_PATH)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

FRAMES = [1, 8, 15, 22, 30]
failures = []


def check(condition, message):
    print(("  PASS  " if condition else "  FAIL  ") + message)
    if not condition:
        failures.append(message)


def close(a, b, tol=1e-4):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def build_mixamo_like_rig(namespace="mixamorig", group=None, animated=True):
    """Hips > Spine > Head, walking forward with side sway, with a skinned cube."""
    if not cmds.namespace(exists=namespace):
        cmds.namespace(add=namespace)
    cmds.select(clear=True)
    hips = cmds.joint(name=namespace + ":Hips", position=(0, 100, 0))
    cmds.joint(name=namespace + ":Spine", position=(0, 120, 0))
    cmds.joint(name=namespace + ":Head", position=(0, 160, 5))
    if group:
        grp = cmds.group(empty=True, name=group)
        cmds.setAttr(grp + ".translate", 10, 0, -5)
        hips = cmds.parent(hips, grp)[0]

    keys = [(1, 3, 100, 0, 0), (8, 6, 97, 40, 10), (15, 0, 95, 80, 20),
            (22, -6, 97, 120, 5), (30, 0, 100, 160, -10)]
    for frame, x, y, z, ry in keys if animated else []:
        cmds.setKeyframe(hips, attribute="translateX", time=frame, value=x)
        cmds.setKeyframe(hips, attribute="translateY", time=frame, value=y)
        cmds.setKeyframe(hips, attribute="translateZ", time=frame, value=z)
        cmds.setKeyframe(hips, attribute="rotateY", time=frame, value=ry)

    cube = cmds.polyCube(width=20, height=80, depth=20, name=namespace + "_body")[0]
    cmds.move(0, 130, 0, cube)
    cmds.skinCluster(namespace + ":Hips", namespace + ":Spine", namespace + ":Head",
                     cube, toSelectedBones=True)
    # Short names stay valid after reparenting (long paths do not).
    return namespace + ":Hips", namespace + ":Head", cube


def sample(head, cube):
    result = []
    for frame in FRAMES:
        cmds.currentTime(frame)
        result.append((cmds.xform(head, query=True, worldSpace=True, translation=True),
                       cmds.pointPosition(cube + ".vtx[5]", world=True)))
    return result


def same_motion(before, after):
    return all(close(h0, h1) and close(v0, v1) for (h0, v0), (h1, v1) in zip(before, after))


def sample_world(node):
    """World translation and rotation of node at each test frame."""
    result = []
    for frame in FRAMES:
        cmds.currentTime(frame)
        result.append((cmds.xform(node, query=True, worldSpace=True, translation=True),
                       cmds.xform(node, query=True, worldSpace=True, rotation=True)))
    return result


def no_leftovers():
    return (not cmds.objExists(tool.TEMP_LOCATOR_NAME)
            and not cmds.ls(type="constraint")
            and not cmds.ls(type="pairBlend"))


def test_basic():
    print("test_basic: one skeleton, nothing selected")
    cmds.file(new=True, force=True)
    hips, head, cube = build_mixamo_like_rig()
    before = sample(head, cube)

    cmds.select(clear=True)
    root = tool.add_root_joint()

    hips = cmds.ls("mixamorig:Hips", long=True)[0]
    check(root == "|root", "root created at top level: " + root)
    check(tool._parent_of(hips) == root, "Hips parented under root")
    check(close(cmds.xform(root, q=True, ws=True, t=True), (0, 0, 0)), "root at world origin")
    check(bool(cmds.listConnections(hips + ".translateZ", type="animCurve")), "Hips animation kept")
    check(same_motion(before, sample(head, cube)), "joint and skinned mesh motion unchanged")

    try:
        cmds.select(clear=True)
        tool.add_root_joint()
        check(False, "second run should be rejected")
    except RuntimeError as error:
        check("already has a root" in str(error), "second run rejected: " + str(error))


def test_selection_and_multiple_skeletons():
    print("test_selection_and_multiple_skeletons")
    cmds.file(new=True, force=True)
    build_mixamo_like_rig("charA")
    build_mixamo_like_rig("charB")

    cmds.select(clear=True)
    try:
        tool.add_root_joint()
        check(False, "should refuse when there are two skeletons and no selection")
    except RuntimeError as error:
        check("2 skeletons" in str(error), "multiple skeletons rejected: " + str(error))

    cmds.select("charB_body")  # select the skinned mesh
    root = tool.add_root_joint()
    check(tool._parent_of(cmds.ls("charB:Hips", long=True)[0]) == root,
          "skinned-mesh selection targets charB")
    check(tool._parent_of(cmds.ls("charA:Hips", long=True)[0]) is None, "charA untouched")


def test_under_group():
    print("test_under_group: Hips inside an offset group")
    cmds.file(new=True, force=True)
    hips, head, cube = build_mixamo_like_rig(group="Armature")
    before = sample(head, cube)

    cmds.select(clear=True)
    root = tool.add_root_joint()
    check(root == "|Armature|root", "root created under the same group: " + root)
    check(same_motion(before, sample(head, cube)), "motion unchanged under group")


def test_menu_command_undo():
    print("test_menu_command_undo")
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True, infinity=True)
    build_mixamo_like_rig()
    cmds.select(clear=True)
    cmds.undoInfo(stateWithoutFlush=True)

    tool._on_add_root()
    check(cmds.objExists("root"), "menu command created root")
    cmds.undo()
    check(not cmds.objExists("root"), "single undo removes root")
    check(tool._parent_of(cmds.ls("mixamorig:Hips", long=True)[0]) is None,
          "single undo restores Hips to top level")


def test_follow_root_default():
    print("test_follow_root_default: follow translate XZ")
    cmds.file(new=True, force=True)
    hips, head, cube = build_mixamo_like_rig()
    before = sample(head, cube)
    hips_before = sample_world(hips)

    cmds.select(clear=True)
    root = tool.add_follow_root()
    root_world = sample_world(root)

    check(tool._parent_of(cmds.ls(hips, long=True)[0]) == root, "Hips parented under root")
    check(same_motion(before, sample(head, cube)), "joint and skinned mesh motion unchanged")
    check(all(close(r[0], (h[0][0], 0, h[0][2])) for r, h in zip(root_world, hips_before)),
          "root = pelvis XZ on the ground, every frame (incl. first)")
    check(all(close(r[1], (0, 0, 0)) for r in root_world), "root does not rotate")
    check(sorted(c.split(":")[-1] for c in tool._anim_curves(root, tool.TRANSFORM_ATTRS))
          == ["root_translateX", "root_translateZ"], "root keyed on translateX/Z only")
    check(close(sample_world(hips)[0][0], hips_before[0][0]), "Hips world position kept")
    check(no_leftovers(), "no temp locator, constraints or pairBlends left")

    try:
        cmds.select(clear=True)
        tool.add_follow_root()
        check(False, "second run should be rejected")
    except RuntimeError as error:
        check("already has a root" in str(error), "second run rejected: " + str(error))


def test_follow_root_all_translate_rotate_y():
    print("test_follow_root_all_translate_rotate_y")
    cmds.file(new=True, force=True)
    hips, head, cube = build_mixamo_like_rig()
    before = sample(head, cube)
    hips_before = sample_world(hips)

    cmds.select(clear=True)
    root = tool.add_follow_root(translate_axes="xyz", rotate_axes="y")
    root_world = sample_world(root)

    check(same_motion(before, sample(head, cube)), "joint and skinned mesh motion unchanged")
    check(all(close(r[0], h[0]) for r, h in zip(root_world, hips_before)),
          "root translation = pelvis world translation")
    check(all(close(r[1], h[1], tol=1e-3) for r, h in zip(root_world, hips_before)),
          "root yaw = pelvis yaw")
    check(no_leftovers(), "no leftovers")


def test_follow_root_under_group():
    print("test_follow_root_under_group: Hips inside an offset group")
    cmds.file(new=True, force=True)
    hips, head, cube = build_mixamo_like_rig(group="Armature")
    before = sample(head, cube)

    cmds.select(clear=True)
    root = tool.add_follow_root()
    check(root == "|Armature|root", "root created under the group: " + root)
    check(same_motion(before, sample(head, cube)), "motion unchanged under group")
    heights = []
    for frame in FRAMES:
        cmds.currentTime(frame)
        heights.append(cmds.getAttr(root + ".translateY"))
    check(all(abs(h) < 1e-4 for h in heights), "root stays on the group's ground (local Y = 0)")


def test_follow_root_errors():
    print("test_follow_root_errors")
    cmds.file(new=True, force=True)
    build_mixamo_like_rig()
    cmds.select(clear=True)
    tool.add_root_joint()
    try:
        cmds.select(clear=True)
        tool.add_follow_root()
        check(False, "should refuse a skeleton that already has a root")
    except RuntimeError as error:
        check("already has a root" in str(error), "existing root rejected: " + str(error))
    check(no_leftovers(), "nothing left behind after the error")

    cmds.file(new=True, force=True)
    build_mixamo_like_rig()
    try:
        cmds.select(clear=True)
        tool.add_follow_root(translate_axes="", rotate_axes="")
        check(False, "should refuse when no axis is chosen")
    except RuntimeError as error:
        check("at least one" in str(error), "no axis rejected: " + str(error))

    cmds.file(new=True, force=True)
    build_mixamo_like_rig(animated=False)
    try:
        cmds.select(clear=True)
        tool.add_follow_root()
        check(False, "should refuse an unanimated pelvis")
    except RuntimeError as error:
        check("no animation" in str(error), "unanimated pelvis rejected: " + str(error))
    check(not cmds.objExists("root"), "no root created after the error")


def test_follow_root_undo():
    print("test_follow_root_undo")
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True, infinity=True)
    build_mixamo_like_rig()
    cmds.select(clear=True)
    cmds.undoInfo(stateWithoutFlush=True)

    tool.run_follow_root("xz", "")
    check(cmds.objExists("root"), "menu command created follow root")
    cmds.undo()
    hips = cmds.ls("mixamorig:Hips", long=True)[0]
    check(not cmds.objExists("root"), "single undo removes root")
    check(tool._parent_of(hips) is None, "single undo restores Hips to top level")
    check(abs(cmds.getAttr(hips + ".translateZ", time=15) - 80) < 1e-4,
          "single undo restores original Hips animation")
    check(no_leftovers(), "no leftovers after undo")


START_OFFSET = (25.0, -40.0)  # pelvis XZ away from the origin


def build_offset_rig(group=None):
    """Mixamo-like rig whose pelvis starts away from the origin."""
    hips, head, cube = build_mixamo_like_rig(group=group)
    cmds.keyframe(hips, attribute="translateX", edit=True, relative=True, valueChange=START_OFFSET[0])
    cmds.keyframe(hips, attribute="translateZ", edit=True, relative=True, valueChange=START_OFFSET[1])
    return hips, head, cube


def shifted_by(before, after, dx, dz):
    """Every sampled position in `after` equals `before` moved by (dx, 0, dz)."""
    move = lambda p: (p[0] + dx, p[1], p[2] + dz)
    return all(close(move(h0), h1) and close(move(v0), v1)
               for (h0, v0), (h1, v1) in zip(before, after))


def check_pelvis_moved(hips, head, cube, before, hips_first_before):
    hips_first = sample_world(hips)[0][0]
    check(close(hips_first, (0, hips_first_before[1], 0)),
          "pelvis at (0, y, 0) on first frame, height kept: {}".format(
              [round(v, 4) for v in hips_first]))
    check(shifted_by(before, sample(head, cube), -hips_first_before[0], -hips_first_before[2]),
          "whole animation shifted by the same offset (motion unchanged)")


def test_move_pelvis_no_root():
    print("test_move_pelvis_no_root")
    cmds.file(new=True, force=True)
    hips, head, cube = build_offset_rig()
    before, hips_first_before = sample(head, cube), sample_world(hips)[0][0]

    cmds.select(clear=True)
    tool.move_pelvis_to_origin()
    check_pelvis_moved(hips, head, cube, before, hips_first_before)


def test_move_pelvis_static_root():
    print("test_move_pelvis_static_root")
    cmds.file(new=True, force=True)
    hips, head, cube = build_offset_rig()
    cmds.select(clear=True)
    root = tool.add_root_joint()
    before, hips_first_before = sample(head, cube), sample_world(hips)[0][0]

    cmds.select(clear=True)
    tool.move_pelvis_to_origin()
    check_pelvis_moved(hips, head, cube, before, hips_first_before)
    check(close(cmds.xform(root, q=True, ws=True, t=True), (0, 0, 0)), "static root stays at origin")


def test_move_pelvis_follow_root():
    print("test_move_pelvis_follow_root")
    cmds.file(new=True, force=True)
    hips, head, cube = build_offset_rig()
    cmds.select(clear=True)
    root = tool.add_follow_root()
    before, hips_first_before = sample(head, cube), sample_world(hips)[0][0]
    hips_curves_before = [cmds.getAttr(hips + ".translateX", time=f) for f in FRAMES]

    cmds.select(clear=True)
    tool.move_pelvis_to_origin()
    check_pelvis_moved(hips, head, cube, before, hips_first_before)
    check(close(sample_world(root)[0][0], (0, 0, 0)), "follow root starts at origin")
    check(close([cmds.getAttr(hips + ".translateX", time=f) for f in FRAMES], hips_curves_before),
          "offset went on the root, pelvis local keys untouched")


def test_move_pelvis_rotated_group():
    print("test_move_pelvis_rotated_group: Hips under a rotated, offset group")
    cmds.file(new=True, force=True)
    hips, head, cube = build_offset_rig(group="Armature")
    cmds.setAttr("Armature.rotateY", 90)
    before, hips_first_before = sample(head, cube), sample_world(hips)[0][0]

    cmds.select(clear=True)
    tool.move_pelvis_to_origin()
    check_pelvis_moved(hips, head, cube, before, hips_first_before)


def test_move_pelvis_unanimated():
    print("test_move_pelvis_unanimated: static pose")
    cmds.file(new=True, force=True)
    hips, head, cube = build_mixamo_like_rig(animated=False)
    cmds.setAttr(hips + ".translate", 12, 90, -7)
    cmds.select(clear=True)
    tool.move_pelvis_to_origin()
    check(close(cmds.xform(hips, q=True, ws=True, t=True), (0, 90, 0)), "static pelvis moved to (0, 90, 0)")


def test_move_pelvis_undo():
    print("test_move_pelvis_undo")
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True, infinity=True)
    hips, head, cube = build_offset_rig()
    cmds.select(clear=True)
    cmds.undoInfo(stateWithoutFlush=True)
    def first_frame_pos():
        # Time changes are undoable in mayapy; keep them out of the undo queue
        # so cmds.undo() below undoes the tool, not this query.
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            cmds.currentTime(FRAMES[0])
            return cmds.xform(hips, query=True, worldSpace=True, translation=True)
        finally:
            cmds.undoInfo(stateWithoutFlush=True)

    first_before = first_frame_pos()

    tool._on_move_pelvis_to_origin()
    check(close(first_frame_pos(), (0, first_before[1], 0)),
          "menu command moved pelvis: {} -> {}".format(first_before, first_frame_pos()))
    cmds.undo()
    check(close(first_frame_pos(), first_before), "single undo restores pelvis position")


def test_plugin_loads():
    print("test_plugin_loads")
    cmds.loadPlugin(PLUGIN_PATH, quiet=True)
    name = os.path.splitext(os.path.basename(PLUGIN_PATH))[0]
    check(cmds.pluginInfo(name, query=True, loaded=True), "plug-in loads")
    cmds.unloadPlugin(name)
    check(not cmds.pluginInfo(name, query=True, loaded=True), "plug-in unloads")


test_basic()
test_selection_and_multiple_skeletons()
test_under_group()
test_menu_command_undo()
test_follow_root_default()
test_follow_root_all_translate_rotate_y()
test_follow_root_under_group()
test_follow_root_errors()
test_follow_root_undo()
test_move_pelvis_no_root()
test_move_pelvis_static_root()
test_move_pelvis_follow_root()
test_move_pelvis_rotated_group()
test_move_pelvis_unanimated()
test_move_pelvis_undo()
test_plugin_loads()

print("\n{} failure(s)".format(len(failures)))
maya.standalone.uninitialize()
sys.exit(1 if failures else 0)
