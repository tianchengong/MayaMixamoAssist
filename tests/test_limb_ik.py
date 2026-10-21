# -*- coding: utf-8 -*-
"""
Headless tests for the Limb IK tools in mixamo_root_tool. Run with mayapy:

    mayapy tests/test_limb_ik.py
"""
import importlib.util
import math
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")

import maya.api.OpenMaya as om  # noqa: E402
import maya.cmds as cmds  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_PATH = os.path.normpath(os.path.join(HERE, "..", "plug-ins", "mixamo_root_tool.py"))

spec = importlib.util.spec_from_file_location("mixamo_root_tool_under_test", PLUGIN_PATH)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

FRAMES = list(range(1, 41))
NS = "mixamorig:"
LIMB_JOINTS = ["LeftArm", "LeftForeArm", "LeftHand", "RightArm", "RightForeArm", "RightHand",
               "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot"]
TIPS = ["LeftHandMiddle1", "RightHandMiddle1", "LeftToeBase", "RightToeBase"]
POS_TOL = 0.01   # cm   (0.1 mm)
ROT_TOL = 0.01   # degrees
failures = []


def check(condition, message):
    print(("  PASS  " if condition else "  FAIL  ") + message)
    if not condition:
        failures.append(message)


# ----------------------------------------------------------------------------
# Test rig
# ----------------------------------------------------------------------------

def build_mixamo_skeleton():
    """Mixamo-like skeleton with non-zero joint orients, twisting arms and
    legs that are fully straight for half of the clip."""
    cmds.file(new=True, force=True)
    cmds.namespace(add="mixamorig")
    cmds.select(clear=True)

    def chain(names_positions, parent):
        cmds.select(parent)
        for name, position in names_positions:
            cmds.joint(name=NS + name, position=position)

    hips = cmds.joint(name=NS + "Hips", position=(0, 100, 0))
    chain([("Spine", (0, 110, 0)), ("Spine1", (0, 120, 0)), ("Spine2", (0, 130, 0))], hips)
    for side, sign in (("Left", 1), ("Right", -1)):
        chain([(side + "Shoulder", (5 * sign, 140, 0)), (side + "Arm", (15 * sign, 140, 0)),
               (side + "ForeArm", (42 * sign, 140, 0)), (side + "Hand", (67 * sign, 140, 0)),
               (side + "HandMiddle1", (75 * sign, 140, 0))], NS + "Spine2")
        chain([(side + "UpLeg", (9 * sign, 95, 0)), (side + "Leg", (9 * sign, 52, 0)),
               (side + "Foot", (9 * sign, 8, 0)), (side + "ToeBase", (9 * sign, 0, 12))], hips)
        cmds.setAttr(NS + side + "Arm.jointOrient", 10 * sign, -20, 15 * sign)
        cmds.setAttr(NS + side + "ForeArm.jointOrient", 0, 5, -10 * sign)
        cmds.setAttr(NS + side + "Leg.jointOrient", 3, 0, 0)

    for f in FRAMES:
        t = (f - 1) / 39.0
        key = lambda name, attr, value: cmds.setKeyframe(NS + name, attribute=attr, time=f, value=value)
        key("Hips", "translateZ", 120 * t)
        key("Hips", "rotateY", 8 * math.sin(t * 6.28))
        key("Spine2", "rotateY", 15 * math.sin(t * 6.28))
        key("Spine2", "rotateX", 5 * math.cos(t * 6.28))
        for side, sign, phase in (("Left", 1, 0.0), ("Right", -1, 3.14)):
            key(side + "Arm", "rotateX", 30 * math.sin(t * 9 + phase))           # upper-arm roll
            key(side + "Arm", "rotateY", -20 + 40 * t)
            key(side + "Arm", "rotateZ", sign * (-60 + 50 * math.sin(t * 3.14)))
            key(side + "ForeArm", "rotateY", -sign * (70 * abs(math.cos(t * 3.14)) + 2))
            key(side + "ForeArm", "rotateX", 40 * math.sin(t * 5 + phase))     # forearm twist
            key(side + "Hand", "rotateZ", 25 * math.sin(t * 7))
            key(side + "Hand", "rotateX", 15 * math.cos(t * 4))
            key(side + "UpLeg", "rotateX", 30 * math.sin(t * 6.28 + phase))
            key(side + "UpLeg", "rotateY", 5 * math.sin(t * 3 + phase))
            key(side + "Leg", "rotateX", max(0.0, 60 * math.sin(t * 6.28 + phase)))  # straight half the time
            key(side + "Foot", "rotateX", 20 * math.sin(t * 6.28 + phase))
    cmds.currentTime(FRAMES[0])
    global ORIGINAL_CONNECTIONS
    ORIGINAL_CONNECTIONS = rotate_connections()


def sample_world():
    """{frame: {joint: (position, quaternion)}} for limb joints and tips."""
    result = {}
    for f in FRAMES:
        cmds.currentTime(f)
        frame = {}
        for name in LIMB_JOINTS + TIPS:
            matrix = om.MTransformationMatrix(om.MMatrix(cmds.xform(NS + name, q=True, ws=True, m=True)))
            frame[name] = (om.MVector(matrix.translation(om.MSpace.kWorld)),
                           matrix.rotation(asQuaternion=True))
        result[f] = frame
    return result


def sample_local():
    """{frame: {plug: value}} of the limb joints' rotate channels."""
    result = {}
    for f in FRAMES:
        cmds.currentTime(f)
        result[f] = {"{}.{}".format(n, a): cmds.getAttr(NS + n + "." + a)
                     for n in LIMB_JOINTS for a in tool.ROTATE_ATTRS}
    return result


def angle_deg(q1, q2):
    d = abs(q1.x * q2.x + q1.y * q2.y + q1.z * q2.z + q1.w * q2.w)
    return math.degrees(2 * math.acos(min(1.0, d)))


def max_errors(before, after, frames=FRAMES, names=LIMB_JOINTS + TIPS):
    pos = max((before[f][n][0] - after[f][n][0]).length() for f in frames for n in names)
    rot = max(angle_deg(before[f][n][1], after[f][n][1]) for f in frames for n in names)
    return pos, rot


def max_local_diff(before, after, frames):
    return max(abs(before[f][p] - after[f][p]) for f in frames for p in before[f])


def no_ik_leftovers():
    return (not cmds.objExists(tool.IK_RIG_GROUP)
            and not cmds.ls(type="animLayer")
            and not cmds.ls(type="constraint")
            and not cmds.ls(type="ikHandle"))


def rotate_connections():
    """{plug: anim curve} for the limb joints' keyed rotate channels."""
    result = {}
    for name in LIMB_JOINTS:
        for attr in tool.ROTATE_ATTRS:
            curves = cmds.listConnections(NS + name + "." + attr, source=True, destination=False,
                                          type="animCurve")
            if curves:
                result[name + "." + attr] = curves[0]
    return result


def curves_connected():
    """Every originally keyed channel is driven by its original curve again."""
    now = rotate_connections()
    return all(now.get(plug) == curve for plug, curve in ORIGINAL_CONNECTIONS.items())


def ctrl(name):
    return cmds.ls(name, long=True)[0]


def add_offset(layer, control, attr, frame, value):
    """The user posing the control on the layer and keying it: the layer's
    own curve gets the offset."""
    curve = cmds.animLayer(layer, query=True, findCurveForPlug="{}.{}".format(control, attr))[0]
    cmds.setKeyframe(curve, time=frame, value=value)


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------

def test_build_reproduces_original():
    print("test_build_reproduces_original: 4 limbs, no edits")
    build_mixamo_skeleton()
    before = sample_world()

    cmds.select(clear=True)
    controls = tool.build_limb_ik()
    check(len(controls) == 4, "4 hand/foot controls created")
    check(cmds.objExists("L_Arm_pole_ctrl") and cmds.objExists("R_Leg_pole_ctrl"), "pole controls created")

    pos, rot = max_errors(before, sample_world())
    check(pos < POS_TOL and rot < ROT_TOL,
          "every limb joint matches the original on every frame (max {:.5f} cm, {:.4f} deg)".format(pos, rot))
    check(not rotate_connections(), "original rotate curves handed over to the rig while it exists")

    spaces = {}
    for limb in tool.LIMB_ORDER:
        targets = cmds.parentConstraint(limb + "_space", query=True, targetList=True) \
            if cmds.listRelatives(limb + "_space", type="parentConstraint") else []
        spaces[limb] = [t.split(":")[-1] for t in targets]
    check(spaces["L_Arm"] == ["Spine2"] and spaces["R_Arm"] == ["Spine2"], "arm controls in chest space")
    check(spaces["L_Leg"] == [] and spaces["R_Leg"] == [], "leg controls in world space")

    try:
        tool.build_limb_ik()
        check(False, "second build should be rejected")
    except RuntimeError as error:
        check("already exists" in str(error), "second build rejected: " + str(error))


def test_other_spaces():
    print("test_other_spaces: arms in pelvis space, legs in chest space")
    build_mixamo_skeleton()
    before = sample_world()
    cmds.select(clear=True)
    tool.build_limb_ik(spaces={"L_Arm": "pelvis", "R_Arm": "world", "L_Leg": "chest", "R_Leg": "pelvis"})
    pos, rot = max_errors(before, sample_world())
    check(pos < POS_TOL and rot < ROT_TOL, "still matches the original (max {:.5f} cm, {:.4f} deg)".format(pos, rot))


def test_edit_layer_range_and_fades():
    print("test_edit_layer_range_and_fades")
    build_mixamo_skeleton()
    before = sample_world()
    cmds.select(clear=True)
    tool.build_limb_ik()
    hand_ctrl = ctrl("L_Arm_ik_ctrl")

    layer = tool.create_edit_layer([hand_ctrl], 15, 25, fade_in=5, fade_out=5)
    weights = [(t, cmds.getAttr(layer + ".weight", time=t)) for t in (10, 15, 20, 25, 30)]
    check([w for _, w in weights] == [0, 1, 1, 1, 0], "layer weight keys 0 / 1 / 1 / 1 / 0 at 10/15/20/25/30")
    pos, rot = max_errors(before, sample_world())
    check(pos < POS_TOL and rot < ROT_TOL, "a new (empty) edit layer changes nothing")

    add_offset(layer, hand_ctrl, "translateX", 20, -4.0)   # 4 cm toward the body, in chest space
    after = sample_world()
    outside = [f for f in FRAMES if f <= 10 or f >= 30]
    pos, rot = max_errors(before, after, frames=outside)
    check(pos < POS_TOL and rot < ROT_TOL, "frames outside range + fades unchanged")
    moved = [(after[f]["LeftHand"][0] - before[f]["LeftHand"][0]).length() for f in FRAMES]
    check(all(abs(moved[f - 1] - 4.0) < 1e-2 for f in range(15, 26)),
          "hand moved 4 cm on every frame of 15-25: {}".format([round(moved[f - 1], 3) for f in (15, 20, 25)]))
    check(0.1 < moved[12 - 1] < 3.9 and 0.1 < moved[28 - 1] < 3.9,
          "fade frames move part of the way (12: {:.2f} cm, 28: {:.2f} cm)".format(moved[11], moved[27]))
    left_arm = ("LeftArm", "LeftForeArm", "LeftHand", "LeftHandMiddle1")
    pos, rot = max_errors(before, after, names=[n for n in LIMB_JOINTS + TIPS if n not in left_arm])
    check(pos < POS_TOL and rot < ROT_TOL, "other limbs unchanged")

    cmds.animLayer(layer, edit=True, mute=True)
    pos, rot = max_errors(before, sample_world())
    check(pos < POS_TOL and rot < ROT_TOL, "muting the layer shows the original animation")
    cmds.animLayer(layer, edit=True, mute=False)


def test_bake_edited_ranges():
    print("test_bake_edited_ranges")
    build_mixamo_skeleton()
    local_before = sample_local()
    cmds.select(clear=True)
    tool.build_limb_ik()
    layer = tool.create_edit_layer([ctrl("L_Arm_ik_ctrl")], 15, 25, fade_in=5, fade_out=5)
    add_offset(layer, ctrl("L_Arm_ik_ctrl"), "translateX", 20, -4.0)
    leg_layer = tool.create_edit_layer([ctrl("R_Leg_pole_ctrl")], 33, 35, fade_in=2, fade_out=3)
    add_offset(leg_layer, ctrl("R_Leg_pole_ctrl"), "translateX", 34, 10.0)
    rigged = sample_world()

    baked = tool.remove_limb_ik("edited")
    check(baked == 21 + 8, "baked frames = 10-30 and 31-38 ({})".format(baked))
    check(no_ik_leftovers(), "rig, layers, constraints and IK handles removed")
    check(curves_connected(), "original rotate curves reconnected")

    local_after = sample_local()
    untouched = [f for f in FRAMES if f < 10 or f > 38]
    check(max_local_diff(local_before, local_after, untouched) == 0.0,
          "frames outside the edited ranges keep their exact original keys")
    pos, rot = max_errors(rigged, sample_world())
    check(pos < POS_TOL and rot < ROT_TOL,
          "baked skeleton matches the rig result on every frame (max {:.5f} cm, {:.4f} deg)".format(pos, rot))


def test_bake_all_without_edits():
    print("test_bake_all_without_edits")
    build_mixamo_skeleton()
    before = sample_world()
    cmds.select(clear=True)
    tool.build_limb_ik(limbs=["L_Arm", "R_Leg"])
    baked = tool.remove_limb_ik("all")
    check(baked == len(FRAMES), "baked every frame ({})".format(baked))
    pos, rot = max_errors(before, sample_world())
    check(pos < POS_TOL and rot < ROT_TOL, "result equals the original (max {:.5f} cm, {:.4f} deg)".format(pos, rot))
    check(no_ik_leftovers() and curves_connected(), "clean scene, curves connected")


def test_discard():
    print("test_discard")
    build_mixamo_skeleton()
    local_before = sample_local()
    cmds.select(clear=True)
    tool.build_limb_ik()
    layer = tool.create_edit_layer([ctrl("L_Arm_ik_ctrl")], 15, 25)
    add_offset(layer, ctrl("L_Arm_ik_ctrl"), "translateY", 20, 5.0)
    baked = tool.remove_limb_ik("none")
    check(baked == 0, "nothing baked")
    check(no_ik_leftovers() and curves_connected(), "clean scene, curves connected")
    check(max_local_diff(local_before, sample_local(), FRAMES) == 0.0, "original animation restored exactly")


def test_errors():
    print("test_errors")
    build_mixamo_skeleton()
    for call, expected, label in (
            (lambda: tool.remove_limb_ik("all"), "No IK rig", "bake without a rig"),
            (lambda: tool.create_edit_layer([], 1, 5), "Select one or more", "edit layer without controls"),
            (lambda: tool.build_limb_ik(limbs=[]), "at least one limb", "no limbs chosen")):
        try:
            cmds.select(clear=True)
            call()
            check(False, label + " should be rejected")
        except RuntimeError as error:
            check(expected in str(error), "{} rejected: {}".format(label, error))

    user_layer = cmds.animLayer("AnimLayer1")
    cmds.animLayer(user_layer, edit=True, attribute=[NS + "LeftHand.rotateX", NS + "LeftHand.rotateZ"])
    try:
        cmds.select(clear=True)
        tool.build_limb_ik(limbs=["L_Arm"])
        check(False, "joint inside a user animation layer should be rejected")
    except RuntimeError as error:
        check("AnimLayer1" in str(error) and "Merge" in str(error),
              "joint in a user animation layer rejected with a fix: " + str(error))
    check(not cmds.objExists(tool.IK_RIG_GROUP), "no rig left behind")
    cmds.delete(user_layer)
    tool._delete_empty_base_layer()

    cmds.rename(NS + "LeftHand", NS + "LeftPalm")
    try:
        cmds.select(clear=True)
        tool.build_limb_ik(limbs=["L_Arm"])
        check(False, "missing joint should be rejected")
    except RuntimeError as error:
        check("LeftHand" in str(error), "missing joint rejected: " + str(error))
    check(no_ik_leftovers(), "nothing left behind")

    build_mixamo_skeleton()
    cmds.select(clear=True)
    tool.build_limb_ik(limbs=["L_Arm"])
    try:
        tool.create_edit_layer([ctrl("L_Arm_ik_ctrl")], 20, 10)
        check(False, "reversed range should be rejected")
    except RuntimeError as error:
        check("before start" in str(error), "reversed range rejected: " + str(error))


def test_build_undo():
    print("test_build_undo")
    build_mixamo_skeleton()
    local_before = sample_local()
    cmds.undoInfo(state=True, infinity=True)
    cmds.select(clear=True)
    cmds.undoInfo(stateWithoutFlush=True)
    tool._run_tool("Mixamo Build Limb IK", tool.build_limb_ik, "built")
    check(cmds.objExists(tool.IK_RIG_GROUP), "rig built through the menu runner")
    cmds.undo()
    check(no_ik_leftovers(), "single undo removes the rig")
    check(curves_connected(), "single undo reconnects the curves")
    check(max_local_diff(local_before, sample_local(), FRAMES) == 0.0, "animation unchanged after undo")


if __name__ == "__main__":
    test_build_reproduces_original()
    test_other_spaces()
    test_edit_layer_range_and_fades()
    test_bake_edited_ranges()
    test_bake_all_without_edits()
    test_discard()
    test_errors()
    test_build_undo()

    print("\n{} failure(s)".format(len(failures)))
    maya.standalone.uninitialize()
    sys.exit(1 if failures else 0)
