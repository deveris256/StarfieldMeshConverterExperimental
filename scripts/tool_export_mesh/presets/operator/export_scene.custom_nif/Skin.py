from utils_blender import RestoreOperatorDefaults
import bpy

op = bpy.context.active_operator
RestoreOperatorDefaults(op)

op.max_border = 2.0
