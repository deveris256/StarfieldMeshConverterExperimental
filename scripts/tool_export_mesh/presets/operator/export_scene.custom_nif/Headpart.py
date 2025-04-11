from utils_blender import RestoreOperatorDefaults
import bpy

op = bpy.context.active_operator
RestoreOperatorDefaults(op)

op.use_internal_geom_data = False
op.is_head_object = 'Auto'
op.max_border = 2.0
op.WEIGHTS = True
