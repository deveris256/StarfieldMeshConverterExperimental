import bpy

import MorphIO

import os

class ImportCustomMorph(bpy.types.Operator):
	bl_idname = "import_scene.custom_morph"
	bl_label = "Import Custom Morph"
	
	filepath: bpy.props.StringProperty(subtype="FILE_PATH")
	filename: bpy.props.StringProperty(default='morph.dat')
	filter_glob: bpy.props.StringProperty(default="*.dat", options={'HIDDEN'})
	use_attributes: bpy.props.BoolProperty(
		name="Import attributes",
		description="Import normals, tangents and colors as attributes.",
		default=False
	)
	debug_delta_normal: bpy.props.BoolProperty(
		name="Debug Delta Normals",
		description="Debug option. DO NOT USE.",
		default=False
	)
	debug_delta_tangent: bpy.props.BoolProperty(
		name="Debug Delta Tangents",
		description="Debug option. DO NOT USE.",
		default=False
	)
	def execute(self, context):
		return MorphIO.ImportMorphFromNumpy(self.filepath, self, self.debug_delta_normal)

	def invoke(self, context, event):
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}

class ExportCustomMorph(bpy.types.Operator):
	bl_idname = "export_scene.custom_morph"
	bl_label = "Export Custom Morph"
	
	filepath: bpy.props.StringProperty(subtype="FILE_PATH")
	filename: bpy.props.StringProperty(default='morph.dat')
	filter_glob: bpy.props.StringProperty(default="*.dat", options={'HIDDEN'})

	use_world_origin = True

	snapping_enabled: bpy.props.BoolProperty(
		name="Snap Morph Data To Selected",
		description="Snapping morph data of connecting vertices to closest verts from selected objects.",
		default=False,
	)

	snapping_range: bpy.props.FloatProperty(
		name="Snapping Range",
		description="Verts from Active Object will copy morph data from verts from selected objects within Snapping Range.",
		default=0.005,
		min=0.0,
		precision=4,
	)

	snap_delta_positions: bpy.props.BoolProperty(
		name="Snap Delta Positions",
		description="Snapping morph delta positions of connecting vertices to closest verts from selected objects.",
		default=False,
	)

	def draw(self, context):
		layout = self.layout
		layout.label(text="Morph Snapping Options:")
		layout.prop(self, "snapping_enabled")
		box = layout.box()
		box.prop(self, "snapping_range")
		box.prop(self, "snap_delta_positions")
		if self.snapping_enabled:
			box.enabled = True
		else:
			box.enabled = False

	def execute(self, context):
		if self.snapping_enabled:
			rtn, _ = MorphIO.ExportMorph_alt(self, context, self.filepath, self, self.snapping_range, self.snap_delta_positions)
		else:
			rtn, _ = MorphIO.ExportMorph_alt(self, context, self.filepath, self)
		return rtn

	def invoke(self, context, event):
		self.filename = "morph.dat"

		if os.path.isdir(os.path.dirname(self.filepath)):
			self.filepath = os.path.join(os.path.dirname(self.filepath),self.filename)

		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}
	
__classes__ = [
	ImportCustomMorph,
    ExportCustomMorph,
]

def menu_func_import_morph(self, context):
	self.layout.operator(
		ImportCustomMorph.bl_idname,
		text="Starfield Morph File (.dat)",
	)

def menu_func_export_morph(self, context):
	self.layout.operator(
		ExportCustomMorph.bl_idname,
		text="Starfield Morph File (.dat)",
	)

def register():
    for cls in __classes__:
        bpy.utils.register_class(cls)
		
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_morph)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export_morph)
		
def unregister():
    for cls in __classes__:
        bpy.utils.unregister_class(cls)

    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_morph)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export_morph)