# -*- coding: utf-8 -*-
"""
Mixamo Root Tool (prototype)

Maya Python plug-in for preparing Mixamo animation for Unreal Engine.

Menu "Mixamo Tools":
    Add Root Joint          Static "root" joint on the ground above the
                            skeleton's top joint (e.g. mixamorig:Hips).
                            Animation is not modified.
    Add Follow Root...      Same root, but baked to follow the pelvis on the
                            chosen translate / rotate axes (root motion).
                            The pelvis is re-baked so the world-space motion
                            of the character is unchanged.
    Move Pelvis to Origin   Shift the whole animation so the pelvis is at
                            world (0, y, 0) on its first frame. Height and
                            motion are kept. Works with no root, a static
                            root, or a follow root.

    Build Limb IK...        Temporary IK rig on the arms / legs that
                            reproduces the original animation exactly:
                            hand/foot controls and elbow/knee pole controls
                            (keyed every frame), driven through an un-keyed
                            driver chain. The original joints keep their twist.
    New Edit Layer...       Additive animation layer for the selected IK
                            controls over a frame range, with the layer weight
                            keyed to fade in and out. Pose the controls on the
                            layer; mute it to compare with the original.
    Bake IK and Remove...   Bake the result back onto the original joints
                            (edited ranges only, or the whole animation) and
                            delete the rig and the edit layers.
    Remove IK (Discard)     Delete the rig and edit layers, restoring the
                            original animation untouched.

Target skeleton:
    - With nothing selected, the only skeleton in the scene is used.
    - Otherwise select any joint, the skeleton group, or the skinned mesh.

Install:
    Windows > Settings/Preferences > Plug-in Manager > Browse... and load
    this file.
"""
import json
import math

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel

PLUGIN_VENDOR = "MixamoTools"
PLUGIN_VERSION = "2.0.0"
MENU_NAME = "MixamoToolsMenu"
FOLLOW_ROOT_WINDOW = "mixamoToolsFollowRootWindow"
BUILD_IK_WINDOW = "mixamoToolsBuildIkWindow"
EDIT_LAYER_WINDOW = "mixamoToolsEditLayerWindow"
DEFAULT_ROOT_NAME = "root"
TEMP_LOCATOR_NAME = "mixamoTools_pelvisWorld_TEMP"

OPTVAR_FOLLOW_TRANSLATE = "mixamoTools_followRootTranslate"
OPTVAR_FOLLOW_ROTATE = "mixamoTools_followRootRotate"
DEFAULT_FOLLOW_TRANSLATE = "xz"
DEFAULT_FOLLOW_ROTATE = ""

AXES = "xyz"
TRANSFORM_ATTRS = ["translateX", "translateY", "translateZ",
                   "rotateX", "rotateY", "rotateZ"]


def maya_useNewAPI():
    """Tell Maya this plug-in uses the Python API 2.0."""
    pass


# ----------------------------------------------------------------------------
# Skeleton lookup
# ----------------------------------------------------------------------------

def _short_name(node):
    """'|grp|mixamorig:Hips' -> 'Hips'"""
    return node.split("|")[-1].split(":")[-1]


def _parent_of(node):
    parents = cmds.listRelatives(node, parent=True, fullPath=True)
    return parents[0] if parents else None


def _top_joint(joint):
    """Walk up the hierarchy while the parent is still a joint."""
    current = joint
    while True:
        parent = _parent_of(current)
        if not parent or cmds.nodeType(parent) != "joint":
            return current
        current = parent


def _unique_top_joints(joints):
    tops = []
    for joint in joints:
        top = _top_joint(joint)
        if top not in tops:
            tops.append(top)
    return tops


def _joints_from_node(node):
    """Collect joints from a selected joint, group, or skinned mesh."""
    if cmds.nodeType(node) == "joint":
        return [node]

    joints = cmds.listRelatives(node, allDescendents=True, type="joint", fullPath=True) or []
    if joints:
        return joints

    history = cmds.listHistory(node, pruneDagObjects=True) or []
    for skin in cmds.ls(history, type="skinCluster"):
        influences = cmds.skinCluster(skin, query=True, influence=True) or []
        joints.extend(cmds.ls(influences, type="joint", long=True))
    return joints


def find_skeleton_top_joint():
    """Return the long name of the top joint of the target skeleton.

    Uses the selection if there is one, otherwise all joints in the scene.
    Raises RuntimeError if no skeleton or more than one skeleton is found.
    """
    selection = cmds.ls(selection=True, long=True) or []
    if selection:
        joints = []
        for node in selection:
            joints.extend(_joints_from_node(node))
        if not joints:
            raise RuntimeError("No joints found in the selection.")
    else:
        joints = cmds.ls(type="joint", long=True) or []
        if not joints:
            raise RuntimeError("No joints found in the scene.")

    tops = _unique_top_joints(joints)
    if len(tops) > 1:
        names = ", ".join(t.split("|")[-1] for t in tops)
        raise RuntimeError(
            "Found {} skeletons ({}). Select one joint of the skeleton you want "
            "and try again.".format(len(tops), names))
    return tops[0]


# ----------------------------------------------------------------------------
# Add Root Joint
# ----------------------------------------------------------------------------

def _check_can_add_root(top_joint, root_name):
    if _short_name(top_joint).lower() == root_name.lower():
        raise RuntimeError("Skeleton already has a root joint: {}".format(top_joint))
    if cmds.objExists(root_name):
        raise RuntimeError(
            'A node named "{}" already exists. Rename or delete it first.'.format(root_name))


def add_root_joint(top_joint=None, root_name=DEFAULT_ROOT_NAME):
    """Create a root joint on the ground and parent the skeleton under it.

    The root is created under the same parent as the top joint with an
    identity transform, so the world-space motion of the skeleton (and any
    skinned mesh) is unchanged.

    Returns the long name of the new root joint.
    """
    if top_joint is None:
        top_joint = find_skeleton_top_joint()
    _check_can_add_root(top_joint, root_name)

    parent = _parent_of(top_joint)
    if parent:
        root = cmds.createNode("joint", name=root_name, parent=parent)
    else:
        root = cmds.createNode("joint", name=root_name)
    root = cmds.ls(root, long=True)[0]

    # Make the root easy to see and pick in the viewport.
    cmds.setAttr(root + ".radius", cmds.getAttr(top_joint + ".radius") * 2.0)

    # relative=True keeps the top joint's local values (and its animation
    # curves) untouched. The root is identity, so world space is unchanged.
    cmds.parent(top_joint, root, relative=True)
    return root


# ----------------------------------------------------------------------------
# Add Follow Root
# ----------------------------------------------------------------------------

def _clean_axes(axes):
    """'ZX' -> 'xz'. Ignores anything that is not an axis letter."""
    axes = (axes or "").lower()
    return "".join(a for a in AXES if a in axes)


def _skip_axes(axes):
    """Axes a constraint should skip, as expected by its 'skip' flag."""
    return [a for a in AXES if a not in axes]


def _animation_range(node):
    times = cmds.keyframe(node, query=True, timeChange=True)
    if not times:
        raise RuntimeError(
            "{} has no animation keys. Nothing for the root to follow.".format(
                node.split("|")[-1]))
    return min(times), max(times)


def _bake(node, attributes, start, end):
    cmds.bakeResults(node, attribute=attributes, time=(start, end), sampleBy=1,
                     simulation=True, disableImplicitControl=True,
                     preserveOutsideKeys=False)


def _delete_constraints(node):
    constraints = cmds.listRelatives(node, type="constraint", fullPath=True)
    if constraints:
        cmds.delete(constraints)


def _anim_curves(node, attributes):
    plugs = ["{}.{}".format(node, attr) for attr in attributes]
    return cmds.listConnections(plugs, source=True, destination=False, type="animCurve") or []


def add_follow_root(top_joint=None, translate_axes=DEFAULT_FOLLOW_TRANSLATE,
                    rotate_axes=DEFAULT_FOLLOW_ROTATE, root_name=DEFAULT_ROOT_NAME):
    """Add a root joint that follows the pelvis, baked over the pelvis' key range.

    translate_axes / rotate_axes: strings such as "xz" or "y". Axes that are
    not followed stay at zero, so with the default "xz" the root slides on the
    ground under the pelvis.

    Steps:
        1. Bake the pelvis' world transform onto a temporary locator. The
           locator breaks the cycle "root follows pelvis, pelvis compensates
           for root".
        2. Constrain the root to the locator on the chosen axes and bake it.
        3. Constrain the pelvis back to the locator and bake it, so its world
           motion is exactly what it was before.
        4. Euler-filter the pelvis rotation and delete the locator.

    Returns the long name of the new root joint.
    """
    translate_axes = _clean_axes(translate_axes)
    rotate_axes = _clean_axes(rotate_axes)
    if not translate_axes and not rotate_axes:
        raise RuntimeError("Choose at least one translate or rotate axis for the root to follow.")

    if top_joint is None:
        top_joint = find_skeleton_top_joint()
    _check_can_add_root(top_joint, root_name)
    start, end = _animation_range(top_joint)

    current_time = cmds.currentTime(query=True)
    root = add_root_joint(top_joint, root_name)
    pelvis = cmds.listRelatives(root, children=True, type="joint", fullPath=True)[0]
    locator = cmds.spaceLocator(name=TEMP_LOCATOR_NAME)[0]
    try:
        # 1. Record the pelvis' world motion.
        cmds.parentConstraint(pelvis, locator, maintainOffset=False)
        _bake(locator, TRANSFORM_ATTRS, start, end)
        _delete_constraints(locator)

        # 2. Root follows the recorded motion on the chosen axes.
        root_attrs = []
        if translate_axes:
            kwargs = {"skip": _skip_axes(translate_axes)} if len(translate_axes) < 3 else {}
            cmds.pointConstraint(locator, root, maintainOffset=False, **kwargs)
            root_attrs += ["translate" + a.upper() for a in translate_axes]
        if rotate_axes:
            kwargs = {"skip": _skip_axes(rotate_axes)} if len(rotate_axes) < 3 else {}
            cmds.orientConstraint(locator, root, maintainOffset=False, **kwargs)
            root_attrs += ["rotate" + a.upper() for a in rotate_axes]
        _bake(root, root_attrs, start, end)
        _delete_constraints(root)

        # 3. Pelvis goes back to its recorded world motion, now relative to the
        #    moving root. Remove its old curves first so the constraint
        #    connects directly instead of through a pairBlend.
        old_curves = _anim_curves(pelvis, TRANSFORM_ATTRS)
        if old_curves:
            cmds.delete(old_curves)
        cmds.parentConstraint(locator, pelvis, maintainOffset=False)
        _bake(pelvis, TRANSFORM_ATTRS, start, end)
        _delete_constraints(pelvis)

        # 4. Avoid 180-degree flips in the re-baked rotation.
        rotate_curves = _anim_curves(pelvis, TRANSFORM_ATTRS[3:])
        if rotate_curves:
            cmds.filterCurve(rotate_curves, filter="euler")
    finally:
        if cmds.objExists(locator):
            cmds.delete(locator)
        cmds.currentTime(current_time)
    return root


# ----------------------------------------------------------------------------
# Move Pelvis to Origin
# ----------------------------------------------------------------------------

def _pelvis_of(top_joint, root_name=DEFAULT_ROOT_NAME):
    """The pelvis is the top joint, or the only child joint of a root."""
    if _short_name(top_joint).lower() != root_name.lower():
        return top_joint
    children = cmds.listRelatives(top_joint, children=True, type="joint", fullPath=True) or []
    if len(children) != 1:
        raise RuntimeError(
            "Root has {} child joints; expected exactly one pelvis.".format(len(children)))
    return children[0]


def _shift_translate(node, offset):
    """Add a constant (x, y, z) offset to node's translate, keys included."""
    for axis, value in zip("XYZ", offset):
        if abs(value) < 1e-9:
            continue
        attr = "translate" + axis
        if _anim_curves(node, [attr]):
            cmds.keyframe(node, attribute=attr, edit=True, relative=True, valueChange=value)
        else:
            cmds.setAttr(node + "." + attr, cmds.getAttr(node + "." + attr) + value)


def move_pelvis_to_origin(top_joint=None, root_name=DEFAULT_ROOT_NAME):
    """Shift the whole animation so the pelvis is at world (0, y, 0) on its first frame.

    Height is kept. The same offset is applied to every frame, so the motion
    itself does not change. The offset goes on:
        - an animated (follow) root, if there is one, so the root also starts
          at the origin;
        - otherwise the pelvis, so a static root stays at the origin.

    Returns the long name of the pelvis.
    """
    if top_joint is None:
        top_joint = find_skeleton_top_joint()
    pelvis = _pelvis_of(top_joint, root_name)

    target = pelvis
    if top_joint != pelvis and _anim_curves(top_joint, ["translateX", "translateZ"]):
        target = top_joint

    times = cmds.keyframe([pelvis, target], query=True, timeChange=True)
    first_frame = min(times) if times else cmds.currentTime(query=True)

    # Evaluate at the first frame by moving the time slider there. getAttr with
    # time= on world matrices can return stale cached values after key edits.
    current_time = cmds.currentTime(query=True)
    cmds.currentTime(first_frame)
    try:
        pelvis_position = cmds.xform(pelvis, query=True, worldSpace=True, translation=True)
        world_offset = om.MVector(-pelvis_position[0], 0.0, -pelvis_position[2])

        # Express the world-space offset in the target's parent space.
        parent = _parent_of(target)
        if parent:
            parent_matrix = om.MMatrix(cmds.xform(parent, query=True, worldSpace=True, matrix=True))
            local_offset = world_offset * parent_matrix.inverse()
        else:
            local_offset = world_offset
    finally:
        cmds.currentTime(current_time)

    _shift_translate(target, (local_offset.x, local_offset.y, local_offset.z))
    return pelvis


# ----------------------------------------------------------------------------
# Limb IK
# ----------------------------------------------------------------------------
#
# Rig layout (everything except the constraints on the original joints lives
# under IK_RIG_GROUP and is deleted when the rig is removed):
#
#   MixamoIK_Rig
#     L_Arm_space            follows the chest / pelvis, or stays in world
#       L_Arm_ik_ctrl        hand control, keyed every frame = original hand
#       L_Arm_pole_ctrl      elbow control, keyed every frame from the elbow
#     L_Arm_drv0..2          un-keyed driver chain solved by the IK handle
#     L_Arm_ikHandle
#     L_Arm_<joint>_ref      original world orientation of upper/lower joint
#
# The original upper/lower joints aim at the driver chain and take their up
# vector from the _ref locators, so with no edits they reproduce the original
# rotation exactly, and with edits they only swing and keep their twist. The
# end joint (hand/foot) follows the control's orientation. Their rotate curves
# are disconnected while the rig exists and reconnected on removal.

IK_RIG_GROUP = "MixamoIK_Rig"
IK_DATA_ATTR = "mixamoIkData"
IK_ROLE_ATTR = "mixamoIkRole"
EDIT_LAYER_ATTR = "mixamoEditLayer"
ROTATE_ATTRS = ["rotateX", "rotateY", "rotateZ"]

# limb -> (Mixamo joint names upper / lower / end, default control space,
#          world direction the elbow/knee points if the limb is always straight)
LIMBS = {
    "L_Arm": (("LeftArm", "LeftForeArm", "LeftHand"), "chest", (0, 0, -1)),
    "R_Arm": (("RightArm", "RightForeArm", "RightHand"), "chest", (0, 0, -1)),
    "L_Leg": (("LeftUpLeg", "LeftLeg", "LeftFoot"), "world", (0, 0, 1)),
    "R_Leg": (("RightUpLeg", "RightLeg", "RightFoot"), "world", (0, 0, 1)),
}
LIMB_ORDER = ["L_Arm", "R_Arm", "L_Leg", "R_Leg"]
SPACES = ["chest", "pelvis", "world"]
CHEST_NAMES = ("Spine2", "Spine1", "Spine")
CONTROL_COLORS = {"L": 6, "R": 13}  # blue / red


def _skeleton_joints(top_joint):
    descendants = cmds.listRelatives(top_joint, allDescendents=True, type="joint", fullPath=True) or []
    return [top_joint] + descendants


def _find_joint(joints, name):
    """Match 'mixamorig:LeftArm', 'LeftArm' or 'mixamorig_LeftArm'."""
    for joint in joints:
        short = _short_name(joint)
        if short == name or short.endswith("_" + name):
            return joint
    return None


def _world_position(node):
    return om.MVector(cmds.xform(node, query=True, worldSpace=True, translation=True))


def _long(node):
    return cmds.ls(node, long=True)[0]


def _space_joint(space, joints, top_joint):
    if space == "world":
        return None
    if space == "pelvis":
        return _pelvis_of(top_joint)
    for name in CHEST_NAMES:
        joint = _find_joint(joints, name)
        if joint:
            return joint
    raise RuntimeError("No chest joint ({}) found for chest space.".format(" / ".join(CHEST_NAMES)))


def _make_control(name, kind, side, size, parent, role):
    if kind == "end":
        ctrl = cmds.circle(name=name, normal=(0, 1, 0), radius=size, constructionHistory=False)[0]
    else:
        ctrl = cmds.spaceLocator(name=name)[0]
    for shape in cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []:
        if kind != "end":
            cmds.setAttr(shape + ".localScale", size, size, size)
        cmds.setAttr(shape + ".overrideEnabled", 1)
        cmds.setAttr(shape + ".overrideColor", CONTROL_COLORS[side])
    cmds.addAttr(ctrl, longName=IK_ROLE_ATTR, dataType="string")
    cmds.setAttr(ctrl + "." + IK_ROLE_ATTR, role, type="string")
    return _long(cmds.parent(ctrl, parent)[0])


def _pole_directions(samples, fallback):
    """Per-frame unit vector from the upper-end line toward the middle joint.

    A (nearly) straight limb has no bend direction; those frames reuse the
    previous bent frame (or the first one), or `fallback` if never bent.
    """
    directions = []
    for a, e, w in samples:
        axis = (w - a).normal()
        offset = (e - a) - axis * ((e - a) * axis)
        limb_length = (e - a).length() + (w - e).length()
        directions.append(offset.normal() if offset.length() > 1e-3 * limb_length else None)

    bent = [d for d in directions if d is not None]
    previous = bent[0] if bent else None
    result = []
    for (a, e, w), direction in zip(samples, directions):
        if direction is None:
            if previous is None:
                axis = (w - a).normal()
                up = om.MVector(fallback)
                previous = (up - axis * (up * axis)).normal()
            direction = previous
        previous = direction
        result.append(direction)
    return result


def _basis_rotation(x, z):
    """Euler rotation (degrees, xyz) of a frame with X = x and Z = z."""
    y = z ^ x
    matrix = om.MMatrix([x.x, x.y, x.z, 0, y.x, y.y, y.z, 0, z.x, z.y, z.z, 0, 0, 0, 0, 1])
    rotation = om.MTransformationMatrix(matrix).rotation()
    return [math.degrees(v) for v in (rotation.x, rotation.y, rotation.z)]


def _build_driver_chain(name, a, e, w, pole_direction, parent):
    """Un-keyed 3-joint chain whose bend axis (local Z) is normal to the limb plane.

    Maya's RP solver only reproduces the original elbow if the chain bends
    in the plane the limb actually lies in.
    """
    x0, x1 = (e - a).normal(), (w - e).normal()
    normal = x0 ^ x1
    if normal.length() < 1e-6:  # straight at every frame: bend toward the pole
        normal = pole_direction ^ x0
    normal = normal.normal()
    bend = math.degrees(x0.angle(x1))

    cmds.select(clear=True)
    j0 = cmds.joint(name=name + "_drv0", position=tuple(a))
    cmds.setAttr(j0 + ".jointOrient", *_basis_rotation(x0, normal))
    j1 = cmds.joint(name=name + "_drv1", relative=True, position=((e - a).length(), 0, 0))
    cmds.joint(name=name + "_drv2", relative=True, position=((w - e).length(), 0, 0))
    cmds.setAttr(j1 + ".rotateZ", bend if bend > 0.1 else 5.0)
    cmds.joint(j0, edit=True, setPreferredAngles=True, children=True)
    cmds.setAttr(j1 + ".rotateZ", 0)
    j0 = _long(cmds.parent(j0, parent)[0])
    j1 = cmds.listRelatives(j0, children=True, type="joint", fullPath=True)[0]
    j2 = cmds.listRelatives(j1, children=True, type="joint", fullPath=True)[0]
    return [j0, j1, j2]


def _aim_and_up(joint, child):
    """Aim axis (toward child) and a perpendicular up axis, in joint space."""
    world = om.MMatrix(cmds.xform(joint, query=True, worldSpace=True, matrix=True))
    aim = ((_world_position(child) - _world_position(joint)) * world.inverse()).normal()
    axes = [om.MVector(1, 0, 0), om.MVector(0, 1, 0), om.MVector(0, 0, 1)]
    up = min(axes, key=lambda v: abs(v * aim))
    up = (up - aim * (up * aim)).normal()
    return tuple(aim), tuple(up)


def _check_rotations_keyable(joint):
    for attr in ROTATE_ATTRS:
        plug = joint + "." + attr
        sources = cmds.listConnections(plug, source=True, destination=False) or []
        if sources and not cmds.ls(sources, type="animCurve"):
            layers = cmds.ls(cmds.listConnections(sources[0]) or [], type="animLayer")
            if layers:
                raise RuntimeError(
                    "{} is in animation layer {}. Merge it into BaseAnimation (Anim Layer "
                    "Editor: Layers > Merge Layers) or remove the joint from that layer, then "
                    "build again. Mixamo Tools edit layers go on the IK controls, not the "
                    "joints.".format(_short_name(joint), layers[0]))
            raise RuntimeError(
                "{} is driven by {}. Limb IK needs plain keyframes on the limb "
                "joints.".format(plug.split("|")[-1], sources[0]))


def build_limb_ik(top_joint=None, limbs=None, spaces=None):
    """Build the IK rig for the given limbs (keys of LIMBS).

    spaces: {limb: "chest" | "pelvis" | "world"}; defaults per LIMBS.
    Returns the long names of the hand/foot controls.
    """
    limbs = [limb for limb in LIMB_ORDER if limbs is None or limb in limbs]
    spaces = spaces or {}
    if not limbs:
        raise RuntimeError("Choose at least one limb.")
    if cmds.objExists(IK_RIG_GROUP):
        raise RuntimeError("An IK rig already exists. Bake or remove it first.")
    if top_joint is None:
        top_joint = find_skeleton_top_joint()
    joints = _skeleton_joints(top_joint)

    chains = {}
    for limb in limbs:
        names, default_space, _ = LIMBS[limb]
        chain = [_find_joint(joints, name) for name in names]
        missing = [name for name, joint in zip(names, chain) if not joint]
        if missing:
            raise RuntimeError("{}: joints not found: {}".format(limb, ", ".join(missing)))
        space = spaces.get(limb, default_space)
        if space not in SPACES:
            raise RuntimeError("Unknown control space: {}".format(space))
        for joint in chain:
            _check_rotations_keyable(joint)
        chains[limb] = {"joints": chain, "space": space,
                        "space_joint": _space_joint(space, joints, top_joint)}

    times = cmds.keyframe([j for c in chains.values() for j in c["joints"]],
                          query=True, timeChange=True)
    if not times:
        raise RuntimeError("The limbs have no animation keys.")
    start, end = int(math.floor(min(times))), int(math.ceil(max(times)))
    frames = list(range(start, end + 1))

    current_time = cmds.currentTime(query=True)
    rig = _long(cmds.group(empty=True, name=IK_RIG_GROUP))
    cmds.addAttr(rig, longName=IK_DATA_ATTR, dataType="string")
    try:
        # 1. Controls and orientation references, matched to the original.
        to_bake, constraints = [], []
        for limb, c in chains.items():
            side = limb[0]
            upper, lower, end_joint = c["joints"]
            size = (_world_position(lower) - _world_position(upper)).length() * 0.35
            space_grp = _long(cmds.group(empty=True, name=limb + "_space", parent=rig))
            if c["space_joint"]:
                cmds.parentConstraint(c["space_joint"], space_grp, maintainOffset=False)
            ctrl = _make_control(limb + "_ik_ctrl", "end", side, size, space_grp, limb + ":end")
            pole = _make_control(limb + "_pole_ctrl", "pole", side, size * 0.5, space_grp, limb + ":pole")
            constraints += cmds.parentConstraint(end_joint, ctrl, maintainOffset=False)
            refs = []
            for joint in (upper, lower):
                ref = cmds.spaceLocator(name="{}_{}_ref".format(limb, _short_name(joint)))[0]
                ref = _long(cmds.parent(ref, rig)[0])
                constraints += cmds.parentConstraint(joint, ref, maintainOffset=False)
                refs.append(ref)
            to_bake += [ctrl] + refs
            c.update(ctrl=ctrl, pole=pole, refs=refs, space_grp=space_grp)
        cmds.bakeResults(to_bake, attribute=TRANSFORM_ATTRS, time=(start, end), sampleBy=1,
                         simulation=True, disableImplicitControl=True, preserveOutsideKeys=False)
        cmds.delete(constraints)

        # 2. Sample the limbs once: pole positions and the most-bent frame.
        samples = {limb: [] for limb in chains}
        space_inverse = {limb: [] for limb in chains}
        for frame in frames:
            cmds.currentTime(frame)
            for limb, c in chains.items():
                samples[limb].append(tuple(_world_position(j) for j in c["joints"]))
                matrix = om.MMatrix(cmds.xform(c["space_grp"], query=True, worldSpace=True, matrix=True))
                space_inverse[limb].append(matrix.inverse())

        for limb, c in chains.items():
            upper, lower, end_joint = c["joints"]
            limb_samples = samples[limb]
            a, e, w = limb_samples[0]
            pole_distance = ((e - a).length() + (w - e).length()) * 0.5
            directions = _pole_directions(limb_samples, LIMBS[limb][2])

            # 3. Pole control keyed every frame from the original elbow/knee.
            for frame, (a, e, w), direction, inverse in zip(
                    frames, limb_samples, directions, space_inverse[limb]):
                local = om.MPoint(e + direction * pole_distance) * inverse
                for axis, value in zip("XYZ", (local.x, local.y, local.z)):
                    cmds.setKeyframe(c["pole"], attribute="translate" + axis, time=frame, value=value)

            # 4. Driver chain built on the most-bent frame, solved by IK.
            bends = [(e - a).normal().angle((w - e).normal()) for a, e, w in limb_samples]
            index = bends.index(max(bends))
            cmds.currentTime(frames[index])
            a, e, w = limb_samples[index]
            drv = _build_driver_chain(limb, a, e, w, directions[index], rig)
            cmds.pointConstraint(upper, drv[0], maintainOffset=False)
            handle = cmds.ikHandle(startJoint=drv[0], endEffector=drv[2], solver="ikRPsolver",
                                   name=limb + "_ikHandle")[0]
            handle = _long(cmds.parent(handle, rig)[0])
            cmds.pointConstraint(c["ctrl"], handle, maintainOffset=False)
            cmds.poleVectorConstraint(c["pole"], handle)
            for node in [drv[0], handle] + c["refs"]:
                cmds.setAttr(node + ".visibility", 0)

            # 5. Hand the original rotations over to the rig.
            aim_up = [_aim_and_up(upper, lower), _aim_and_up(lower, end_joint)]
            c["curves"], c["static"] = {}, {}
            for joint in c["joints"]:
                for attr in ROTATE_ATTRS:
                    plug = joint + "." + attr
                    curves = cmds.listConnections(plug, source=True, destination=False, type="animCurve")
                    if curves:
                        cmds.disconnectAttr(curves[0] + ".output", plug)
                        c["curves"][plug] = curves[0]
                    else:
                        c["static"][plug] = cmds.getAttr(plug)
            for joint, target, ref, (aim, up) in zip((upper, lower), drv[1:], c["refs"], aim_up):
                cmds.aimConstraint(target, joint, aimVector=aim, upVector=up,
                                   worldUpType="objectrotation", worldUpObject=ref, worldUpVector=up)
            cmds.orientConstraint(c["ctrl"], end_joint, maintainOffset=False)

        data = {"start": start, "end": end, "limbs": {
            limb: {"joints": c["joints"], "curves": c["curves"], "static": c["static"],
                   "space": c["space"]}
            for limb, c in chains.items()}}
        cmds.setAttr(rig + "." + IK_DATA_ATTR, json.dumps(data), type="string")
    except Exception:
        # Leave the scene as it was: reconnect what was disconnected, drop the rig.
        for c in chains.values():
            for joint in c["joints"]:
                _delete_constraints(joint)
            for plug, curve in c.get("curves", {}).items():
                cmds.connectAttr(curve + ".output", plug, force=True)
        if cmds.objExists(rig):
            cmds.delete(rig)
        raise
    finally:
        cmds.currentTime(current_time)
    return [c["ctrl"] for c in chains.values()]


def _ik_rig_data():
    if not cmds.objExists(IK_RIG_GROUP):
        raise RuntimeError("No IK rig in the scene. Use Build Limb IK first.")
    return json.loads(cmds.getAttr(IK_RIG_GROUP + "." + IK_DATA_ATTR))


def _edit_layers():
    return [layer for layer in cmds.ls(type="animLayer") or []
            if cmds.attributeQuery(EDIT_LAYER_ATTR, node=layer, exists=True)]


def _edited_frames(start, end):
    """Frames covered by the weight curves of the unmuted edit layers."""
    frames = set()
    for layer in _edit_layers():
        if cmds.animLayer(layer, query=True, mute=True):
            continue
        times = cmds.keyframe(layer + ".weight", query=True, timeChange=True) or []
        if times:
            frames.update(range(int(math.floor(min(times))), int(math.ceil(max(times))) + 1))
    return sorted(f for f in frames if start <= f <= end)


def _ik_control_role(node):
    if cmds.attributeQuery(IK_ROLE_ATTR, node=node, exists=True):
        return cmds.getAttr(node + "." + IK_ROLE_ATTR)
    return None


def selected_ik_controls():
    return [node for node in cmds.ls(selection=True, long=True) or [] if _ik_control_role(node)]


def create_edit_layer(controls, start, end, fade_in=5, fade_out=5, falloff="ease"):
    """Additive animation layer for `controls`, weighted to the frame range.

    The weight is 0 before start - fade_in, ramps to 1 at start, holds to end
    and ramps back to 0 at end + fade_out. The layer gets a zero key in the
    middle of the range and becomes the active layer, so posing the controls
    and keying them (S) stores the edit on this layer.

    Returns the name of the new layer.
    """
    if not controls:
        raise RuntimeError("Select one or more IK controls (hand/foot or pole) first.")
    start, end, fade_in, fade_out = int(start), int(end), int(fade_in), int(fade_out)
    if end < start:
        raise RuntimeError("End frame is before start frame.")
    if fade_in < 1 or fade_out < 1:
        raise RuntimeError("Fade in / out must be at least 1 frame.")

    attrs = []
    for ctrl in controls:
        role = _ik_control_role(ctrl)
        if not role:
            raise RuntimeError("{} is not an IK control.".format(ctrl.split("|")[-1]))
        channels = TRANSFORM_ATTRS if role.endswith(":end") else TRANSFORM_ATTRS[:3]
        attrs += ["{}.{}".format(ctrl, attr) for attr in channels]

    label = _short_name(controls[0]).replace("_ctrl", "")
    layer = cmds.animLayer("MixamoEdit_{}_{}_{}".format(label, start, end), override=False)
    cmds.addAttr(layer, longName=EDIT_LAYER_ATTR, attributeType="bool", defaultValue=True)
    cmds.animLayer(layer, edit=True, attribute=attrs)

    tangent = "flat" if falloff == "ease" else "linear"
    for time, value in ((start - fade_in, 0), (start, 1), (end, 1), (end + fade_out, 0)):
        cmds.setKeyframe(layer, attribute="weight", time=time, value=value,
                         inTangentType=tangent, outTangentType=tangent)

    # Zero offset key. setKeyframe's value= on a layer is the *result* value,
    # so key first and then set the layer's own curve to 0.
    middle = (start + end) // 2
    for plug in attrs:
        node, attr = plug.rsplit(".", 1)
        cmds.setKeyframe(node, attribute=attr, animLayer=layer, time=middle)
        curve = cmds.animLayer(layer, query=True, findCurveForPlug=plug)[0]
        cmds.keyframe(curve, edit=True, absolute=True, valueChange=0.0)

    for other in cmds.ls(type="animLayer"):
        cmds.animLayer(other, edit=True, selected=False, preferred=False)
    cmds.animLayer(layer, edit=True, selected=True, preferred=True)
    cmds.currentTime(middle)
    return layer


def _delete_empty_base_layer():
    root = cmds.animLayer(query=True, root=True)
    if root and not (cmds.animLayer(root, query=True, children=True) or []):
        cmds.delete(root)


def remove_limb_ik(bake="none"):
    """Remove the IK rig and edit layers.

    bake: "none"   discard all edits, the original animation comes back;
          "edited" key the rig's result on the frames covered by the unmuted
                   edit layers, other frames keep their original keys;
          "all"    key the rig's result on every frame.
    Returns the number of baked frames.
    """
    data = _ik_rig_data()
    start, end = data["start"], data["end"]
    if bake == "all":
        frames = list(range(start, end + 1))
    elif bake == "edited":
        frames = _edited_frames(start, end)
    elif bake == "none":
        frames = []
    else:
        raise RuntimeError("Unknown bake mode: {}".format(bake))

    limbs = data["limbs"].values()
    plugs = [j + "." + attr for limb in limbs for j in limb["joints"] for attr in ROTATE_ATTRS]
    values = {}
    current_time = cmds.currentTime(query=True)
    try:
        for frame in frames:
            cmds.currentTime(frame)
            for plug in plugs:
                values[plug, frame] = cmds.getAttr(plug)
    finally:
        cmds.currentTime(current_time)

    for limb in limbs:
        for joint in limb["joints"]:
            _delete_constraints(joint)
    for layer in _edit_layers():
        cmds.delete(layer)
    _delete_empty_base_layer()
    cmds.delete(IK_RIG_GROUP)

    for limb in limbs:
        for plug, curve in limb["curves"].items():
            if cmds.objExists(curve):
                cmds.connectAttr(curve + ".output", plug, force=True)
        for plug, value in limb["static"].items():
            cmds.setAttr(plug, value)
            if frames:
                # The channel gets a curve now; pin every unedited frame to
                # the original static value so interpolation cannot move it.
                node, attr = plug.rsplit(".", 1)
                for frame in range(start, end + 1):
                    if frame not in frames:
                        cmds.setKeyframe(node, attribute=attr, time=frame, value=value)

    # Key the baked values, each frame using the Euler solution closest to the
    # frame before it, so there are no flips and untouched keys stay as they are.
    frame_set = set(frames)
    for limb in limbs:
        for joint in limb["joints"]:
            order = cmds.getAttr(joint + ".rotateOrder")
            joint_plugs = [joint + "." + attr for attr in ROTATE_ATTRS]
            previous = None
            for frame in frames:
                if previous is None or frame - 1 not in frame_set:
                    previous = om.MEulerRotation(
                        [math.radians(cmds.getAttr(p, time=frame - 1)) for p in joint_plugs], order)
                euler = om.MEulerRotation(
                    [math.radians(values[p, frame]) for p in joint_plugs], order).closestSolution(previous)
                for attr, value in zip(ROTATE_ATTRS, (euler.x, euler.y, euler.z)):
                    cmds.setKeyframe(joint, attribute=attr, time=frame, value=math.degrees(value))
                previous = euler
    return len(frames)


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------

def _report(message):
    print("[Mixamo Tools] " + message)
    if not cmds.about(batch=True):
        cmds.inViewMessage(assistMessage=message, position="topCenter", fade=True)


def _run_tool(chunk_name, func, success_message, select_result=True):
    """Run func() as a single undo step, report success or show a warning.

    success_message is formatted with the function's return value, which is
    selected afterwards if select_result is set.
    """
    cmds.undoInfo(openChunk=True, chunkName=chunk_name)
    try:
        result = func()
        if select_result:
            cmds.select(result, replace=True)
    except RuntimeError as error:
        cmds.warning("[Mixamo Tools] {}".format(error))
        return None
    finally:
        cmds.undoInfo(closeChunk=True)
    _report(success_message.format(result))
    return result


def _on_add_root(*_):
    _run_tool("Mixamo Add Root Joint", add_root_joint, "Added root joint: {}")


def run_follow_root(translate_axes, rotate_axes):
    return _run_tool(
        "Mixamo Add Follow Root",
        lambda: add_follow_root(translate_axes=translate_axes, rotate_axes=rotate_axes),
        "Added follow root: {}")


def _on_move_pelvis_to_origin(*_):
    _run_tool("Mixamo Move Pelvis to Origin", move_pelvis_to_origin,
              "Moved pelvis to origin on first frame: {}")


def _get_option(name, default):
    if cmds.optionVar(exists=name):
        return cmds.optionVar(query=name)
    return default


def _axes_from_checkboxes(group):
    values = cmds.checkBoxGrp(group, query=True, valueArray3=True)
    return "".join(axis for axis, on in zip(AXES, values) if on)


def _on_follow_root_apply(translate_group, rotate_group, *_):
    translate_axes = _axes_from_checkboxes(translate_group)
    rotate_axes = _axes_from_checkboxes(rotate_group)
    cmds.optionVar(stringValue=(OPTVAR_FOLLOW_TRANSLATE, translate_axes))
    cmds.optionVar(stringValue=(OPTVAR_FOLLOW_ROTATE, rotate_axes))
    run_follow_root(translate_axes, rotate_axes)


def _show_follow_root_window(*_):
    if cmds.window(FOLLOW_ROOT_WINDOW, exists=True):
        cmds.deleteUI(FOLLOW_ROOT_WINDOW)

    translate_axes = _get_option(OPTVAR_FOLLOW_TRANSLATE, DEFAULT_FOLLOW_TRANSLATE)
    rotate_axes = _get_option(OPTVAR_FOLLOW_ROTATE, DEFAULT_FOLLOW_ROTATE)

    window = cmds.window(FOLLOW_ROOT_WINDOW, title="Add Follow Root",
                         sizeable=False, resizeToFitChildren=True)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8, columnAttach=("both", 12))
    cmds.separator(style="none", height=4)
    cmds.text(label="Root follows the pelvis on the checked axes.\n"
                    "Unchecked axes stay at 0 (on the ground).",
              align="left")
    translate_group = cmds.checkBoxGrp(
        numberOfCheckBoxes=3, label="Root Follow Translate",
        labelArray3=["X", "Y", "Z"], columnWidth4=(130, 50, 50, 50),
        valueArray3=[axis in translate_axes for axis in AXES])
    rotate_group = cmds.checkBoxGrp(
        numberOfCheckBoxes=3, label="Root Follow Rotate",
        labelArray3=["X", "Y", "Z"], columnWidth4=(130, 50, 50, 50),
        valueArray3=[axis in rotate_axes for axis in AXES])
    cmds.rowLayout(numberOfColumns=2, columnWidth2=(150, 150), columnAttach2=("both", "both"))
    cmds.button(label="Apply",
                command=lambda *_: _on_follow_root_apply(translate_group, rotate_group))
    cmds.button(label="Close", command=lambda *_: cmds.deleteUI(window))
    cmds.setParent("..")
    cmds.separator(style="none", height=4)
    cmds.showWindow(window)


def _on_build_ik(limb_group, arm_space_menu, leg_space_menu, *_):
    enabled = cmds.checkBoxGrp(limb_group, query=True, valueArray4=True)
    limbs = [limb for limb, on in zip(LIMB_ORDER, enabled) if on]
    arm_space = cmds.optionMenuGrp(arm_space_menu, query=True, value=True).lower()
    leg_space = cmds.optionMenuGrp(leg_space_menu, query=True, value=True).lower()
    spaces = {limb: arm_space if limb.endswith("Arm") else leg_space for limb in limbs}
    _run_tool("Mixamo Build Limb IK", lambda: build_limb_ik(limbs=limbs, spaces=spaces),
              "Built limb IK. Select a control and use New Edit Layer to edit a range.")


def _space_menu(label, default):
    menu = cmds.optionMenuGrp(label=label, columnWidth2=(130, 100))
    for space in SPACES:
        cmds.menuItem(label=space.capitalize())
    cmds.optionMenuGrp(menu, edit=True, value=default.capitalize())
    return menu


def _show_build_ik_window(*_):
    if cmds.window(BUILD_IK_WINDOW, exists=True):
        cmds.deleteUI(BUILD_IK_WINDOW)
    window = cmds.window(BUILD_IK_WINDOW, title="Build Limb IK",
                         sizeable=False, resizeToFitChildren=True)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8, columnAttach=("both", 12))
    cmds.separator(style="none", height=4)
    cmds.text(label="IK controls start exactly on the original animation.\n"
                    "Control space = what the hand/foot offsets are relative to.",
              align="left")
    limb_group = cmds.checkBoxGrp(
        numberOfCheckBoxes=4, label="Limbs", labelArray4=["L Arm", "R Arm", "L Leg", "R Leg"],
        valueArray4=[True, True, True, True], columnWidth5=(130, 60, 60, 60, 60))
    arm_space_menu = _space_menu("Arm Control Space", "chest")
    leg_space_menu = _space_menu("Leg Control Space", "world")
    cmds.rowLayout(numberOfColumns=2, columnWidth2=(170, 170), columnAttach2=("both", "both"))
    cmds.button(label="Build",
                command=lambda *_: _on_build_ik(limb_group, arm_space_menu, leg_space_menu))
    cmds.button(label="Close", command=lambda *_: cmds.deleteUI(window))
    cmds.setParent("..")
    cmds.separator(style="none", height=4)
    cmds.showWindow(window)


def _time_slider_range():
    """Highlighted time slider range (shift-drag), or the current frame."""
    slider = mel.eval("$_mixamoTmp = $gPlayBackSlider")
    if cmds.timeControl(slider, query=True, rangeVisible=True):
        start, end = cmds.timeControl(slider, query=True, rangeArray=True)
        return int(start), int(end) - 1
    frame = int(cmds.currentTime(query=True))
    return frame, frame


def _fill_range_from_slider(range_fields, *_):
    start, end = _time_slider_range()
    cmds.intFieldGrp(range_fields, edit=True, value1=start, value2=end)


def _on_create_edit_layer(range_fields, fade_fields, falloff_group, *_):
    start = cmds.intFieldGrp(range_fields, query=True, value1=True)
    end = cmds.intFieldGrp(range_fields, query=True, value2=True)
    fade_in = cmds.intFieldGrp(fade_fields, query=True, value1=True)
    fade_out = cmds.intFieldGrp(fade_fields, query=True, value2=True)
    falloff = "ease" if cmds.radioButtonGrp(falloff_group, query=True, select=True) == 1 else "linear"
    controls = selected_ik_controls()
    _run_tool("Mixamo New Edit Layer",
              lambda: create_edit_layer(controls, start, end, fade_in, fade_out, falloff) and controls,
              "Edit layer ready. Pose the controls and key them (S); mute the layer to compare.")


def _show_edit_layer_window(*_):
    if cmds.window(EDIT_LAYER_WINDOW, exists=True):
        cmds.deleteUI(EDIT_LAYER_WINDOW)
    start, end = _time_slider_range()
    window = cmds.window(EDIT_LAYER_WINDOW, title="New Edit Layer",
                         sizeable=False, resizeToFitChildren=True)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8, columnAttach=("both", 12))
    cmds.separator(style="none", height=4)
    cmds.text(label="Select IK controls, then create a layer for a frame range.\n"
                    "Outside the range plus fades, the original animation is kept.",
              align="left")
    range_fields = cmds.intFieldGrp(numberOfFields=2, label="Start / End",
                                    value1=start, value2=end, columnWidth3=(110, 70, 70))
    cmds.button(label="Get Range From Time Slider (Shift-Drag)",
                command=lambda *_: _fill_range_from_slider(range_fields))
    fade_fields = cmds.intFieldGrp(numberOfFields=2, label="Fade In / Out",
                                   value1=5, value2=5, columnWidth3=(110, 70, 70))
    falloff_group = cmds.radioButtonGrp(numberOfRadioButtons=2, label="Falloff",
                                        labelArray2=["Ease", "Linear"], select=1,
                                        columnWidth3=(110, 70, 70))
    cmds.rowLayout(numberOfColumns=2, columnWidth2=(170, 170), columnAttach2=("both", "both"))
    cmds.button(label="Create Layer",
                command=lambda *_: _on_create_edit_layer(range_fields, fade_fields, falloff_group))
    cmds.button(label="Close", command=lambda *_: cmds.deleteUI(window))
    cmds.setParent("..")
    cmds.separator(style="none", height=4)
    cmds.showWindow(window)


def _on_bake_ik(*_):
    if not cmds.objExists(IK_RIG_GROUP):
        cmds.warning("[Mixamo Tools] No IK rig in the scene.")
        return
    choice = cmds.confirmDialog(
        title="Bake IK and Remove",
        message="Bake the IK result onto the skeleton, then delete the IK rig and edit layers.\n\n"
                "Edited Ranges: frames covered by unmuted edit layers; all other frames\n"
                "keep their original keys.\n"
                "Whole Animation: every frame.",
        button=["Edited Ranges", "Whole Animation", "Cancel"],
        defaultButton="Edited Ranges", cancelButton="Cancel", dismissString="Cancel")
    if choice == "Cancel":
        return
    mode = "edited" if choice == "Edited Ranges" else "all"
    _run_tool("Mixamo Bake IK", lambda: remove_limb_ik(mode),
              "Baked {} frames onto the skeleton and removed the IK rig.", select_result=False)


def _on_discard_ik(*_):
    if not cmds.objExists(IK_RIG_GROUP):
        cmds.warning("[Mixamo Tools] No IK rig in the scene.")
        return
    choice = cmds.confirmDialog(
        title="Remove IK (Discard Edits)",
        message="Delete the IK rig and edit layers without baking?\n"
                "The original animation is restored.",
        button=["Remove", "Cancel"], defaultButton="Cancel",
        cancelButton="Cancel", dismissString="Cancel")
    if choice == "Remove":
        _run_tool("Mixamo Remove IK", lambda: remove_limb_ik("none"),
                  "Removed the IK rig; original animation restored.", select_result=False)


def _delete_ui():
    for window in (FOLLOW_ROOT_WINDOW, BUILD_IK_WINDOW, EDIT_LAYER_WINDOW):
        if cmds.window(window, exists=True):
            cmds.deleteUI(window)
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME, menu=True)


def _create_menu():
    _delete_ui()
    main_window = mel.eval("$_mixamoTmp = $gMainWindow")
    cmds.menu(MENU_NAME, label="Mixamo Tools", parent=main_window, tearOff=True)
    cmds.menuItem(
        label="Add Root Joint",
        annotation="Add a static root joint on the ground above the skeleton's top joint",
        command=_on_add_root)
    cmds.menuItem(
        label="Add Follow Root...",
        annotation="Add a root joint that follows the pelvis (root motion)",
        command=_show_follow_root_window)
    cmds.menuItem(
        label="Move Pelvis to Origin",
        annotation="Shift the animation so the pelvis is at (0, y, 0) on the first frame",
        command=_on_move_pelvis_to_origin)
    cmds.menuItem(divider=True, dividerLabel="Limb IK")
    cmds.menuItem(
        label="Build Limb IK...",
        annotation="Temporary IK controls on arms/legs that match the original animation",
        command=_show_build_ik_window)
    cmds.menuItem(
        label="New Edit Layer...",
        annotation="Additive layer for the selected IK controls over a frame range, with fades",
        command=_show_edit_layer_window)
    cmds.menuItem(
        label="Bake IK and Remove...",
        annotation="Bake the IK result onto the skeleton and delete the rig and edit layers",
        command=_on_bake_ik)
    cmds.menuItem(
        label="Remove IK (Discard Edits)",
        annotation="Delete the IK rig and edit layers; the original animation comes back",
        command=_on_discard_ik)
    cmds.menuItem(divider=True)
    cmds.menuItem(label="Version {}".format(PLUGIN_VERSION), enable=False)


# ----------------------------------------------------------------------------
# Plug-in entry points
# ----------------------------------------------------------------------------

def initializePlugin(plugin):
    om.MFnPlugin(plugin, PLUGIN_VENDOR, PLUGIN_VERSION, "Any")
    if not cmds.about(batch=True):
        _create_menu()


def uninitializePlugin(plugin):
    om.MFnPlugin(plugin)
    if not cmds.about(batch=True):
        _delete_ui()
