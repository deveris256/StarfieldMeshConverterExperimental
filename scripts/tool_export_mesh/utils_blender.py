import bpy
import bmesh
import os
import numpy as np
import mathutils

import utils_math
import utils_common as utils
import CapsuleGenGeoNode as capsule_gen
import PlaneGenGeoNode as plane_gen

read_only_marker = '[READONLY]'
mix_normal = False

bone_rename_dict = {}

def UtilsFolderPath():
	utils_path = _update_path(os.path.dirname(__file__))
	return utils_path

def PluginAssetsFolderPath():
	utils_path = UtilsFolderPath()
	return os.path.join(utils_path, 'Assets')

def DefaultResultFolderPath():
	utils_path = UtilsFolderPath()
	result_path = os.path.join(utils_path, 'Results')
	
	if not os.path.isdir(result_path):
		os.makedirs(result_path)
    
	return result_path

def TempFolderPath():
	utils_path = UtilsFolderPath()
	temp_path = os.path.join(utils_path, 'Temp')
	
	if not os.path.isdir(temp_path):
		os.makedirs(temp_path)
		
	return temp_path

def ThirdPartyFolderPath():
	utils_path = UtilsFolderPath()
	third_party_path = os.path.join(utils_path, '3rdParty')
	
	if not os.path.isdir(third_party_path):
		os.makedirs(third_party_path)
		
	return third_party_path

def open_folder(initial_directory):

	# Set the window manager to the current context
	wm = bpy.context.window_manager

	# Call the file browser operator
	bpy.ops.wm.path_open(filepath=initial_directory)
						 
def _update_path(utils_p):
	utils_path = bpy.path.abspath(utils_p)
	return utils_path

def SetBSGeometryName(obj:bpy.types.Object, name:str):
	obj['BSGeometry_Name'] = name

def GetBSGeometryName(obj:bpy.types.Object):
	if 'BSGeometry_Name' in obj.keys():
		return obj['BSGeometry_Name']
	else:
		return obj.name

def SetSelectObjects(objs):
	original_selected = bpy.context.selected_objects
	bpy.ops.object.select_all(action='DESELECT')
	for obj in objs:
		if obj != None:
			obj.select_set(state=True)
	return original_selected

def GetActiveObject():
	return bpy.context.active_object

def SetActiveObject(obj, deselect_all = False):
	original_active = GetActiveObject()
	if deselect_all:
		bpy.ops.object.select_all(action='DESELECT')
	if obj != None:
		obj.select_set(True)
	bpy.context.view_layer.objects.active = obj
	return original_active

def GetSharpGroups(selected_obj):
	sharp_edge_vertices = []
	# Ensure we are in Edit Mode
	if selected_obj.mode != 'EDIT':
		bpy.ops.object.mode_set(mode='EDIT')

	# Deselect all
	bpy.ops.mesh.select_all(action='DESELECT')

	_obj = bpy.context.edit_object
	_me = _obj.data

	_bm = bmesh.from_edit_mesh(_me)
	for e in _bm.edges:
		if not e.smooth:
			e.select = True
	
	bmesh.update_edit_mesh(_me)

	# Switch to Vertex Select Mode
	bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='VERT')

	# Switch back to Object Mode
	bpy.ops.object.mode_set(mode='OBJECT')

	# Iterate through selected vertices and add their indices to the list
	for vertex in selected_obj.data.vertices:
		if vertex.select:
			sharp_edge_vertices.append(vertex.index)

	return sharp_edge_vertices

def TriangulateMesh(mesh_obj:bpy.types.Object, make_copy = True) -> bpy.types.Object:
	if make_copy:
		mesh_obj = mesh_obj.copy()
		mesh_obj.data = mesh_obj.data.copy()
		bpy.context.collection.objects.link(mesh_obj)
		bpy.context.view_layer.objects.active = mesh_obj

	# Use bmesh to triangulate the mesh
	bm = bmesh.new()
	bm.from_mesh(mesh_obj.data)
	bmesh.ops.triangulate(bm, faces=bm.faces)
	bm.to_mesh(mesh_obj.data)
	bm.free()

	return mesh_obj

def ClearEmptyVertexGroups(obj:bpy.types.Object, vertex_groups:list[str] = None):
    '''
    Removes all vertex groups that have no vertices assigned to them.
    :param obj: The object to remove empty vertex groups from.
    :param vertex_groups: A list of vertex group names to remove. If None, all empty vertex groups will be removed.
    '''
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    deform_layer = bm.verts.layers.deform.active

    if vertex_groups == None:
        orig_vg_indices = [vg.index for vg in obj.vertex_groups]
    else:
        orig_vg_indices = [obj.vertex_groups[vg_name].index for vg_name in vertex_groups]

    vg_indices = set()
    for v in bm.verts:
        d_vert = v[deform_layer]
        vert_vg_indices = set()
        for vg_index in orig_vg_indices:
            rtn = d_vert.get(vg_index)
            if rtn is not None and rtn != 0:
                vert_vg_indices.add(vg_index)
        vg_indices = vg_indices.union(vert_vg_indices)

    vg_to_remove = [obj.vertex_groups[vg_index] for vg_index in orig_vg_indices if vg_index not in vg_indices]
    
    for vg in vg_to_remove:
        obj.vertex_groups.remove(vg)

    bm.free()

def AverageSelectedVertWeight(obj:bpy.types.Object, vertex_groups:list[str] = None):
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    deform_layer = bm.verts.layers.deform.active

    if vertex_groups == None:
        weight_dict = {vg.index: 0 for vg in obj.vertex_groups}
    else:
        weight_dict = {obj.vertex_groups[vg_name].index:0 for vg_name in vertex_groups}
    
    count = 0
    for v in bm.verts:
        if not v.select:
            continue
        count += 1
        d_vert = v[deform_layer]
        for vg_index, weight in d_vert.items():
            if vg_index in weight_dict:
                weight_dict[vg_index] += weight
                
    weight_dict = {key: value/count for key, value in weight_dict.items()}
    
    for v in bm.verts:
        if not v.select:
            continue
        d_vert = v[deform_layer]
        for vg_index, weight in weight_dict.items():
            d_vert[vg_index] = weight

    bm.to_mesh(obj.data)
    bm.free()

def HomographyWarp(mesh_obj:bpy.types.Object, source_pts, target_pts, mask_vg_name = None, invert_mask = False, as_shape_key = False, shape_key_name = 'HOMOGRAPHY_WARP'):
	'''
	Warp the mesh using homography transformation.
	:param mesh_obj: The mesh object to warp.
	:param source_pts: The source points.
	:param target_pts: The target points.
	:param mask_vg_name: The vertex group name to mask the warping.
	'''
	# If has this shape key, remove it
	if as_shape_key and shape_key_name in [sk.name for sk in mesh_obj.data.shape_keys.key_blocks]:
		mesh_obj.shape_key_remove(mesh_obj.data.shape_keys.key_blocks[shape_key_name])

	matrix_world = np.array(mesh_obj.matrix_world)
	matrix_world_inv = np.linalg.inv(matrix_world)

	mask_vg_index = None
	if mask_vg_name != None:
		if mask_vg_name not in [vg.name for vg in mesh_obj.vertex_groups]:
			print(f"Vertex group {mask_vg_name} does not exist in the object.")
		else:
			mask_vg_index = mesh_obj.vertex_groups[mask_vg_name].index

	# Use bmesh to triangulate the mesh
	bm = bmesh.new()
	bm.from_mesh(mesh_obj.data)

	deform_layer = bm.verts.layers.deform.active

	if mask_vg_index != None:
		if invert_mask:
			mask_vg_verts = [(v, 1 - v[deform_layer][mask_vg_index]) if mask_vg_index in v[deform_layer].keys() else (v, 1) for v in bm.verts]
		else:
			mask_vg_verts = [(v, v[deform_layer][mask_vg_index]) for v in bm.verts if mask_vg_index in v[deform_layer].keys()]
	else:
		mask_vg_verts = [(v, 1) for v in bm.verts]

	# Get the homography matrix
	H = utils_math.estimate_homography_3d(np.array(source_pts), np.array(target_pts))

	# Warp the mesh
	vertices = np.array([v.co for v, _ in mask_vg_verts])
	weights = np.array([weight for _, weight in mask_vg_verts])

	# Convert to homogeneous coordinates
	vertices_homogeneous = np.hstack((vertices, np.ones((vertices.shape[0], 1))))

	# Apply the world transformation
	transformed_vertices = (matrix_world @ vertices_homogeneous.T)

	# Apply the homography transformation
	transformed_vertices_homography = (H @ transformed_vertices).T

	# Normalize the coordinates to convert back to 3D
	transformed_vertices_homography /= transformed_vertices_homography[:, 3][:, np.newaxis]

	# Apply the inverse world transformation
	final_transformed_vertices = (matrix_world_inv @ transformed_vertices_homography.T).T

	# Linear interpolation between the original and transformed positions
	interpolated_vertices = (1 - weights)[:, np.newaxis] * vertices + weights[:, np.newaxis] * final_transformed_vertices[:, :3]

	for i, (v, _) in enumerate(mask_vg_verts):
		v.co = interpolated_vertices[i, :3]

	if not as_shape_key:
		bm.to_mesh(mesh_obj.data)
	else:
		# Create a shape key
		# If no basis shape key, create one
		if mesh_obj.data.shape_keys == None:
			mesh_obj.shape_key_add(name='Basis')
		shape_key = mesh_obj.shape_key_add(name=shape_key_name)
		shape_key.data.foreach_set('co', [co for v in bm.verts for co in v.co])
		shape_key.value = 1
	bm.free()

def HomographyWarpFromBoxes(mesh_obj:bpy.types.Object, source_box:bpy.types.Object, target_box:bpy.types.Object, mask_vg_name = None, invert_mask = False, as_shape_key = False, shape_key_name = 'HOMOGRAPHY_WARP'):
	# Make sure the boxes have the same number of vertices (8)
	if len(source_box.data.vertices) != len(target_box.data.vertices) or len(source_box.data.vertices) != 8:
		print("Boxes must have the same number of vertices which is 8.")
		return
	
	source_pts = [source_box.matrix_world @ v.co for v in source_box.data.vertices]
	target_pts = [target_box.matrix_world @ v.co for v in target_box.data.vertices]

	HomographyWarp(mesh_obj, source_pts, target_pts, mask_vg_name, invert_mask, as_shape_key, shape_key_name)

def CombineVertexGroups(obj:bpy.types.Object, vertex_groups:list[str], new_name:str, delete_old = False, skip_if_not_exist = True, combine_mode = 'ADD'):
    if len(vertex_groups) == 0:
        print("No vertex groups to combine.")
        return

    # Check if new_name already exists
    combined_vg = None
    if new_name in [vg.name for vg in obj.vertex_groups]:
        combined_vg = obj.vertex_groups[new_name]
    else:
        combined_vg = obj.vertex_groups.new(name = new_name)

    # Check if all vertex groups exist
    skip_list = []
    for vg_name in vertex_groups:
        if vg_name not in [vg.name for vg in obj.vertex_groups]:
            if skip_if_not_exist:
                print(f"Vertex group {vg_name} does not exist in the object.")
                skip_list.append(vg_name)
            else:
                print(f"Vertex group {vg_name} does not exist in the object.")
                return
    
    if len(vertex_groups) == len(skip_list):
        print("No vertex groups to combine.")
        return

    combine_vg_index = combined_vg.index
    vg_indices = [obj.vertex_groups[vg_name].index for vg_name in vertex_groups if vg_name not in skip_list]
        
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    
    deform_layer = bm.verts.layers.deform.active

    for v in bm.verts:
        d_vert = v[deform_layer]
        weights = [d_vert[vg_index] for vg_index in vg_indices if vg_index in d_vert]
        if combine_mode == 'ADD':
            new_weight = sum(weights)
        elif combine_mode == 'MAX':
            new_weight = max(weights)
        combined_vg.add([v.index], new_weight, 'REPLACE')

    bm.free()

    if delete_old:
        for vg_name in vertex_groups:
            if vg_name not in skip_list and vg_name != new_name:
                obj.vertex_groups.remove(obj.vertex_groups[vg_name])


def SubtractVertexGroups(obj:bpy.types.Object, vertex_groups:list[str], target_vg_name:str, skip_if_not_exist = True):
	if len(vertex_groups) == 0:
		print("No vertex groups to combine.")
		return

	# Check if new_name already exists
	combined_vg = None
	if target_vg_name in [vg.name for vg in obj.vertex_groups]:
		combined_vg = obj.vertex_groups[target_vg_name]
	else:
		return

	# Check if all vertex groups exist
	skip_list = []
	for vg_name in vertex_groups:
		if vg_name not in [vg.name for vg in obj.vertex_groups]:
			if skip_if_not_exist:
				print(f"Vertex group {vg_name} does not exist in the object.")
				skip_list.append(vg_name)
			else:
				print(f"Vertex group {vg_name} does not exist in the object.")
				return

	if len(vertex_groups) == len(skip_list):
		print("No vertex groups to combine.")
		return

	combine_vg_index = combined_vg.index
	vg_indices = [obj.vertex_groups[vg_name].index for vg_name in vertex_groups if vg_name not in skip_list]
		
	bm = bmesh.new()
	bm.from_mesh(obj.data)

	deform_layer = bm.verts.layers.deform.active

	for v in bm.verts:
		d_vert = v[deform_layer]
		base_weight = d_vert[combine_vg_index] if combine_vg_index in d_vert else 0
		weights = [d_vert[vg_index] for vg_index in vg_indices if vg_index in d_vert]
		combined_vg.add([v.index], max(0, base_weight-sum(weights)), 'REPLACE')

	bm.free()

def ApplyTransform(mesh_obj:bpy.types.Object):
	prev_active = SetActiveObject(mesh_obj)
	bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
	SetActiveObject(prev_active)

def PreprocessAndProxy(old_obj, use_world_origin, convert_to_mesh = True, do_triangulation = True, auto_add_sharp = False):
	if not (old_obj and old_obj.type == 'MESH'):
		print("No valid object is selected.")
		return None, None
	
	if old_obj.data.uv_layers == None or len(old_obj.data.uv_layers) == 0 or old_obj.data.uv_layers.active == None:
		print(f"Your model has no active UV map! Please create one before exporting.")
		return None, None

	if old_obj.data.shape_keys != None and old_obj.data.shape_keys.key_blocks != None:
		key_blocks = old_obj.data.shape_keys.key_blocks
		if key_blocks[0].name == 'Basis':
			keys = old_obj.data.shape_keys.key_blocks.keys()
			shape_key_index = keys.index('Basis')
			old_obj.active_shape_key_index = shape_key_index

	new_obj = old_obj.copy()
	new_obj.data = old_obj.data.copy()
	new_obj.animation_data_clear()
	bpy.context.collection.objects.link(new_obj)
	bpy.context.view_layer.objects.active = new_obj

	SetActiveObject(new_obj, True)
	if convert_to_mesh:
		bpy.ops.object.convert(target='MESH', merge_customdata=False) # A rare bug causing inconsistence between model and morph

	new_obj.data.validate()

	# Mesh clean up
	if new_obj.mode != 'EDIT':
		bpy.ops.object.mode_set(mode='EDIT')

	# Select all
	bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='VERT')
	bpy.ops.mesh.select_all(action='SELECT')
	bpy.ops.mesh.delete_loose()
	bpy.ops.mesh.select_all(action='DESELECT')

	bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='EDGE')
	
	# Select all edges
	bpy.ops.mesh.select_all(action='SELECT')
	
	# Switch to Edge Select mode in UV Editor
	bpy.ops.uv.select_all(action='SELECT')
	
	bpy.ops.uv.seams_from_islands()
	
	bpy.ops.object.mode_set(mode='OBJECT')

	base_obj = new_obj.copy()
	base_obj.data = new_obj.data.copy()
	base_obj.animation_data_clear()
	bpy.context.collection.objects.link(base_obj)
	bpy.context.view_layer.objects.active = base_obj

	SetActiveObject(base_obj, True)
	bpy.ops.object.mode_set(mode='EDIT')
	bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='EDGE')
	bpy.ops.mesh.select_all(action='DESELECT')
	bpy.ops.mesh.select_non_manifold(extend=False, use_boundary=True, use_multi_face = False,use_non_contiguous = False, use_verts = False)
	bpy.ops.mesh.remove_doubles(use_sharp_edge_from_normals=True)
	bpy.ops.object.mode_set(mode='OBJECT')

	SetActiveObject(new_obj, True)
	bpy.ops.object.mode_set(mode='EDIT')
	bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='EDGE')
	bpy.ops.mesh.select_all(action='DESELECT')
	bpy.ops.mesh.select_non_manifold(extend=False, use_boundary=True, use_multi_face = False,use_non_contiguous = False, use_verts = False)

	__obj = bpy.context.edit_object
	__me = __obj.data

	__bm = bmesh.from_edit_mesh(__me)
	for __e in __bm.edges:
		if __e.select:
			__e.smooth = False


	bmesh.update_edit_mesh(__me)
	
	__bm.free()

	bpy.ops.mesh.edge_split(type='EDGE')
	
	bpy.ops.mesh.select_all(action='DESELECT')
	
	bpy.ops.object.mode_set(mode='OBJECT')

	if use_world_origin:
		bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
	
	selected_obj = new_obj
	
	
	bpy.ops.object.select_all(action='DESELECT')
	selected_obj.select_set(True)

	bpy.ops.object.shade_auto_smooth(use_auto_smooth=True)

	if auto_add_sharp:
		modifier2 = selected_obj.modifiers.new(name = selected_obj.name, type='DATA_TRANSFER')
		modifier2.object = base_obj
		#modifier2.vertex_group = double_faces_vg.name
		#modifier2.invert_vertex_group = True
		modifier2.use_loop_data = True
		modifier2.data_types_loops = {'CUSTOM_NORMAL'}
		modifier2.use_max_distance = True
		modifier2.max_distance = 0.001
		modifier2.loop_mapping = "NEAREST_POLYNOR"

		#modifier3 = selected_obj.modifiers.new(name = selected_obj.name, type='DATA_TRANSFER')
		bpy.ops.object.modifier_apply(modifier=modifier2.name)

	# Create a BMesh from the selected object
	bm = bmesh.new()
	bm.from_mesh(selected_obj.data)
	
	seams = [e for e in bm.edges if e.seam or not e.smooth]

	# split on seams
	bmesh.ops.split_edges(bm, edges=seams)

	if do_triangulation:
		bmesh.ops.triangulate(bm, faces=bm.faces)

	bm.to_mesh(selected_obj.data)
	bm.free()

	modifier2 = selected_obj.modifiers.new(name = selected_obj.name, type='DATA_TRANSFER')
	modifier2.object = base_obj
	modifier2.use_loop_data = True
	modifier2.data_types_loops = {'CUSTOM_NORMAL'}
	modifier2.use_max_distance = True
	modifier2.max_distance = 0.001
	modifier2.loop_mapping = "NEAREST_POLYNOR"

	#modifier3 = selected_obj.modifiers.new(name = selected_obj.name, type='DATA_TRANSFER')
	bpy.ops.object.modifier_apply(modifier=modifier2.name)
	
	bpy.data.meshes.remove(base_obj.data)
	
	return old_obj, selected_obj
	
def IsReadOnly(obj):
	return read_only_marker in obj.name

def IsMesh(obj):
	return obj.type == 'MESH'

def GetSelectedObjs(exclude_active = True) -> list[bpy.types.Object]:
	l = []
	for obj in bpy.context.selected_objects:
		if not exclude_active or obj != bpy.context.active_object:
			l.append(obj)
	return l

def move_object_to_collection(objs: list[bpy.types.Object], coll: bpy.types.Collection):
	for obj in objs:
		if obj != None:
			old_colls = [c for c in obj.users_collection]
			for c in old_colls:
				if c != None:
					c.objects.unlink(obj)
			coll.objects.link(obj)

def new_collection(name, do_link = True):
	coll = bpy.data.collections.new(name)
	if do_link:
		bpy.context.scene.collection.children.link(coll)
	return coll

def remove_collection(coll, hierarchy = True):
	if hierarchy:
		for obj in coll.objects:
			bpy.data.objects.remove(obj, do_unlink=True)

	bpy.data.collections.remove(coll)

def move_object_to_parent(objs, parent):
	for obj in objs:
		if obj != None and parent != None:
			obj.parent = parent


def SmoothPerimeterNormal(active_obj, selected_obj_list, apply_as_mesh = False, base_obj = None, loop_mapping_base = "TOPOLOGY"):
	if active_obj in selected_obj_list:
		selected_obj_list.remove(active_obj)
	
	if apply_as_mesh:
		original_active = SetActiveObject(active_obj)

	if base_obj:
		modifier = active_obj.modifiers.new(name = base_obj.name, type='DATA_TRANSFER')
		modifier.object = base_obj
		if 'DOUBLE_FACES_VERTS' in active_obj.vertex_groups:
			modifier.vertex_group = 'DOUBLE_FACES_VERTS'
			modifier.invert_vertex_group = True
		modifier.use_loop_data = True
		modifier.data_types_loops = {'CUSTOM_NORMAL'}
		modifier.use_max_distance = True
		modifier.max_distance = 0.001
		modifier.loop_mapping = loop_mapping_base
		if apply_as_mesh:
			bpy.ops.object.modifier_apply(modifier=modifier.name)

	for target_obj in selected_obj_list:
		if target_obj is not None:
			modifier = active_obj.modifiers.new(name = target_obj.name, type='DATA_TRANSFER')
			modifier.object = target_obj
			if 'DOUBLE_FACES_VERTS' in active_obj.vertex_groups:
				modifier.vertex_group = 'DOUBLE_FACES_VERTS'
				modifier.invert_vertex_group = True
			modifier.use_loop_data = True
			modifier.data_types_loops = {'CUSTOM_NORMAL'}
			modifier.use_max_distance = True
			modifier.max_distance = 0.001
			if mix_normal:
				modifier.mix_mode = 'ADD'
			if apply_as_mesh:
				bpy.ops.object.modifier_apply(modifier=modifier.name)

	if apply_as_mesh:
		SetActiveObject(original_active)

def CalcVIdLIdlist(mesh):
	vid_lid_list = [0 for _ in range(len(mesh.vertices))]
	for face in mesh.polygons:
		for l_id in face.loop_indices:
			vid_lid_list[mesh.loops[l_id].vertex_index] = l_id
	return vid_lid_list

def GetNormalTangents(mesh, with_tangent = True, fast_mode = False, fast_mode_list = None):
	if fast_mode and fast_mode_list != None:
		if with_tangent:
			mesh.calc_tangents()		
			Normals = [np.array(mesh.corner_normals[loop_idx]) for loop_idx in fast_mode_list]
			
			if with_tangent:
				Bitangent_sign = [mesh.loops[loop_idx].bitangent_sign for loop_idx in fast_mode_list]
				Tangents = [utils_math.GramSchmidtOrthogonalize(np.array(mesh.loops[loop_idx].tangent), n) for loop_idx, n in zip(fast_mode_list, Normals)]
				return np.array(Normals), np.array(Tangents), np.array(Bitangent_sign)
	
			return np.array(Normals), None, None
	else:
		verts_count = len(mesh.vertices)
		Normals = [np.array([0,0,0]) for i in range(verts_count)]
		if with_tangent:
			Bitangent_sign = [1 for i in range(verts_count)]
			Tangents = [np.array([0,0,0]) for i in range(verts_count)]
			mesh.calc_tangents()
		for face in mesh.polygons:
			for vert_idx, loop_idx in zip(face.vertices, face.loop_indices):
				Normals[vert_idx] = Normals[vert_idx] + np.array(mesh.corner_normals[loop_idx])
				if with_tangent:
					Bitangent_sign[vert_idx] = mesh.loops[loop_idx].bitangent_sign
					Tangents[vert_idx] = Tangents[vert_idx] + np.array(mesh.loops[loop_idx].tangent)

		_Normals = [utils_math.NormalizeVec(n) for n in Normals]

		if with_tangent:
			_Tangents = [utils_math.GramSchmidtOrthogonalize(t, np.array(n)) for t, n in zip(Tangents, _Normals)]
			return np.array(_Normals), np.array(_Tangents), np.array(Bitangent_sign)
		else:
			_Tangents = None
			Bitangent_sign = None
			return np.array(_Normals), None, None

def VisualizeVectors(obj_mesh, offsets, vectors, name = "Vectors"):
	vis_obj = None
	bm = bmesh.new()
	bm.from_mesh(obj_mesh)
	num_tangents = len(vectors)

	if len(offsets) == 0:
		offsets = [(0,0,0) for i in range(num_tangents)]
	if num_tangents != len(bm.verts):
		print("Cannot create vector vis due to vertex number mismatch.")
		pass
	else:
		mesh = bpy.data.meshes.new(name)  # add a new mesh
		obj = bpy.data.objects.new(name, mesh)  # add a new object using the mesh

		bpy.context.collection.objects.link(obj)
		scale = 0.02
		origins = [(offset[0] + v.co[0], offset[1] + v.co[1], offset[2] + v.co[2]) for v, offset in zip(bm.verts, offsets)]
		verts = origins + [(t[0] * scale + o[0], t[1] * scale + o[1], t[2] * scale + o[2]) for t, o in zip(vectors, origins)]
		edges = [[i,i + len(bm.verts)] for i in range(len(bm.verts))]
		mesh.from_pydata(verts, edges, [])
		vis_obj = obj

	bm.free()
	return vis_obj

def SetWeightKeys(obj, weight_keys:list):
	if len(weight_keys) != len(obj.vertex_groups):
		min_len = min(len(weight_keys), len(obj.vertex_groups))
		for vg, name in zip(obj.vertex_groups[:min_len], weight_keys[:min_len]):
			vg.name = name
		return False

	for vg, name in zip(obj.vertex_groups, weight_keys):
		vg.name = name

	return True

def BoxFromMinMax(name: str, min_position: list, max_position: list):
	mesh = bpy.data.meshes.new(name)  # add a new mesh
	obj = bpy.data.objects.new(name, mesh)  # add a new object using the mesh
	bpy.context.collection.objects.link(obj)
	minx = min_position[0]
	miny = min_position[1]
	minz = min_position[2]
	maxx = max_position[0]
	maxy = max_position[1]
	maxz = max_position[2]
	verts = [(minx, miny, minz),(minx, miny, maxz),(maxx, miny, minz),(maxx, miny, maxz),(minx, maxy, minz),(minx, maxy, maxz),(maxx, maxy, minz),(maxx, maxy, maxz)]
	faces = [[0,1,3,2], [4,5,7,6], [0,1,5,4], [2,3,7,6], [0,2,6,4], [1,3,7,5]]
	mesh.from_pydata(verts, [], faces)
	return obj

def BoxFromCenterExpand(name: str, center: list, expand: list):
	min_position = [c - e for c, e in zip(center, expand)]
	max_position = [c + e for c, e in zip(center, expand)]

	return BoxFromMinMax(name, min_position, max_position)

def SphereFromCenterRadius(name: str, center: list, radius):
	obj = BoxFromCenterExpand(name, center, [radius, radius, radius])
	obj.display_type = 'BOUNDS'
	obj.display_bounds_type = 'SPHERE'

	return obj

def GetObjBBoxMinMax(obj):
	bbox = obj.bound_box
	xs = [i[0] for i in bbox]
	ys = [i[1] for i in bbox]
	zs = [i[2] for i in bbox]
	mins = [min(xs), min(ys), min(zs)]
	maxs = [max(xs), max(ys), max(zs)]
	return mins, maxs

def GetObjBBoxCenterExpand(obj):
	mins, maxs = GetObjBBoxMinMax(obj)
	center = [(ma + mi) * 0.5 for mi, ma in zip(mins, maxs)]
	expand = [ma - ce for ma, ce in zip(maxs, center)]
	return center, expand

def ApplyAllModifiers(obj):
	selected = GetSelectedObjs(True)
	active = SetActiveObject(obj)

	for modifier in obj.modifiers:
		bpy.ops.object.modifier_apply(modifier=modifier.name)

	SetSelectObjects(selected)
	SetActiveObject(active)

def GetVertColorPerVert(obj):
    vert_number = len(obj.data.vertices)
    v_colors = [(1,1,1,1) for i in range(vert_number)]
    mesh = obj.data
    try:
        color_layer = mesh.vertex_colors[0]
    except:
        return v_colors, False
    mesh_loops = {li: loop.vertex_index for li, loop in enumerate(mesh.loops)}
    vtx_colors = {mesh_loops[li]: data.color for li, data in color_layer.data.items()}
    for idx, color in vtx_colors.items():
        v_colors[idx] = color

    return v_colors, True

def SetVertColorPerVert(obj, v_colors):
	col = obj.data.vertex_colors.active
	for poly in obj.data.polygons:
		for v_ix, l_ix in zip(poly.vertices, poly.loop_indices):
			col.data[l_ix].color = v_colors[v_ix]

def ColorToRGB888(color):
	rgb = list(color)[:-1]
	return [int(v * 255) for v in rgb]

def RGB888ToColor(rgb) -> tuple:
	return tuple([v / 255 for v in rgb] + [1])

def RGB888ToRGB565(rgb):
	r = rgb[0] >> 3
	g = rgb[1] >> 2
	b = rgb[2] >> 3
	return (r << 11) | (g << 5) | b

def RenamingBone(name:str):
	tags = utils._tag(name)
	if 'RI' in tags and name.startswith("R_"):
		bone_rename_dict[name[2:] + '.R'] = 1
		return name[2:] + '.R'
	elif 'LE' in tags and name.startswith("L_"):
		bone_rename_dict[name[2:] + '.L'] = 1
		return name[2:] + '.L'
	
	return name

def RenamingBoneList(names:list):
	return [RenamingBone(name) for name in names]

def RevertRenamingBone(name:str):
	#if name in bone_rename_dict and bone_rename_dict[name] > 0:
	#	bone_rename_dict[name] -= 1
	#else:
	#	return name

	if name.endswith('.R'):
		if name.startswith('R_'):
			return name[:-2]
		else:
			return 'R_' + name[:-2]
	elif name.endswith('.L'):
		if name.startswith('L_'):
			return name[:-2]
		else:
			return 'L_' + name[:-2]
	return name

def RevertRenamingBoneList(names:list):
	return [RevertRenamingBone(name) for name in names]

def BuildhkBufferedMesh(mesh: dict, hkaSkeleton_obj: bpy.types.Object):
	mesh_objs = []
	mesh_type = mesh['type']
	name = mesh['name']
	print(f'Reading havok cloth sim mesh: {name}.')
	matrix = mesh['localFrame']
	print(f'Local frame: {matrix}')
	T = mathutils.Matrix()
	for i in range(4):
		for j in range(4):
			T[i][j] = mesh['localFrame'][i][j]

	if mesh_type == 1: # Capsule
		mesh_obj = CapsuleFromParameters(name, mesh['capsuleEnd'], mesh['capsuleStart'], mesh['capsuleSmallRadius'], mesh['capsuleBigRadius'])
		mesh_obj.matrix_world = T
		mesh_objs.append(mesh_obj)
	elif mesh_type == 0:
		positions = mesh['positions']
		normals = mesh['normals']
		triangles = mesh['triangleIndices']
		boneWeights = utils.TransformWeightData(mesh['boneWeights'], do_normalize=True)
		bl_mesh = bpy.data.meshes.new(name = name)
		bl_mesh.from_pydata(positions, [], triangles)
		mesh_obj = bpy.data.objects.new(name, bl_mesh)
		bpy.context.scene.collection.objects.link(mesh_obj)
		mesh_obj.matrix_world = T

		for b_id, b_entry in boneWeights.items():
			vg = mesh_obj.vertex_groups.new(name = hkaSkeleton_obj.data.bones[b_id].name)
			for v_id, weight in b_entry:
				vg.add([v_id], weight, 'REPLACE')


		vis_obj = VisualizeVectors(bl_mesh, [], normals, 'normals')
		mesh_objs.append(mesh_obj)
		mesh_objs.append(vis_obj)


	if 'extraShapes' in mesh.keys():
		for sub_mesh in mesh['extraShapes']:
			mesh_objs.extend(BuildhkBufferedMesh(sub_mesh, hkaSkeleton_obj))

	return mesh_objs

def GetNodeGroupInputIdentifier(node_group, input_name):
	inputs = node_group.inputs
	id = inputs[input_name].identifier
	return id

def CapsuleFromParameters(name: str, smallEnd: list, bigEnd: list, smallRadius: float, bigRadius: float):
	mesh = bpy.data.meshes.new(name)
	obj = bpy.data.objects.new(name, mesh)

	bpy.context.collection.objects.link(obj)

	if smallRadius > bigRadius:
		smallRadius, bigRadius = bigRadius, smallRadius
		smallEnd, bigEnd = bigEnd, smallEnd

	gnmod = None
	for gnmod in obj.modifiers:
		if gnmod.type == "NODES":
			break

	if (gnmod is None) or (gnmod.type != "NODES"):
		gnmod = obj.modifiers.new("Capsule", "NODES")

	gnmod.node_group = capsule_gen.GetGeoNode()

	start_big_id = GetNodeGroupInputIdentifier(gnmod.node_group, "Start/Big")
	end_small_id = GetNodeGroupInputIdentifier(gnmod.node_group, "End/Small")
	big_radius_id = GetNodeGroupInputIdentifier(gnmod.node_group, "bigRadius")
	small_radius_id = GetNodeGroupInputIdentifier(gnmod.node_group, "smallRadius")

	gnmod[start_big_id][0] = bigEnd[0]
	gnmod[start_big_id][1] = bigEnd[1]
	gnmod[start_big_id][2] = bigEnd[2]

	gnmod[end_small_id][0] = smallEnd[0]
	gnmod[end_small_id][1] = smallEnd[1]
	gnmod[end_small_id][2] = smallEnd[2]

	gnmod[big_radius_id] = bigRadius
	gnmod[small_radius_id] = smallRadius
	
	obj.display_type = 'WIRE'
	return obj

def SetCapsuleParameters(capsule_obj, smallEnd = None, bigEnd = None, smallRadius = None, bigRadius = None, create_if_not_exist = True):
	if capsule_obj == None:
		return
	gnmod = None
	for gnmod in capsule_obj.modifiers:
		if gnmod.type == "NODES" and gnmod.node_group.name == "Capsule_Gen":
			break

	if (gnmod is None) or (gnmod.type != "NODES") or (gnmod.node_group.name != "Capsule_Gen"):
		if not create_if_not_exist:
			return
		gnmod = capsule_obj.modifiers.new("Capsule", "NODES")

	if bigEnd is not None:
		start_big_id = GetNodeGroupInputIdentifier(gnmod.node_group, "Start/Big")
		gnmod[start_big_id][0] = bigEnd[0]
		gnmod[start_big_id][1] = bigEnd[1]
		gnmod[start_big_id][2] = bigEnd[2]

	if smallEnd is not None:
		end_small_id = GetNodeGroupInputIdentifier(gnmod.node_group, "End/Small")
		gnmod[end_small_id][0] = smallEnd[0]
		gnmod[end_small_id][1] = smallEnd[1]
		gnmod[end_small_id][2] = smallEnd[2]

	if bigRadius is not None:
		big_radius_id = GetNodeGroupInputIdentifier(gnmod.node_group, "bigRadius")
		gnmod[big_radius_id] = bigRadius

	if smallRadius is not None:
		small_radius_id = GetNodeGroupInputIdentifier(gnmod.node_group, "smallRadius")
		gnmod[small_radius_id] = smallRadius

	gnmod.show_on_cage = True
	gnmod.show_on_cage = False

def ConstraintObjToArmatureBone(obj, armature_obj, bone_index, inherit_rotation = False):
	if armature_obj == None:
		return
	if obj == None:
		return
	if bone_index == None:
		return

	obj.constraints.clear()
	bone= armature_obj.data.bones[bone_index]
	if inherit_rotation:
		obj.matrix_world = armature_obj.matrix_world @ bone.matrix_local
	arma_const = obj.constraints.new(type = 'ARMATURE')
	_target = arma_const.targets.new()
	_target.target = armature_obj
	_target.subtarget = bone.name

def PlaneFromOriginNormal(name: str, origin, normal_dir, size = 1.0):
	mesh = bpy.data.meshes.new(name)
	obj = bpy.data.objects.new(name, mesh)

	bpy.context.collection.objects.link(obj)

	gnmod = None
	for gnmod in obj.modifiers:
		if gnmod.type == "NODES":
			break

	if (gnmod is None) or (gnmod.type != "NODES"):
		gnmod = obj.modifiers.new("Plane", "NODES")

	gnmod.node_group = plane_gen.GetGeoNode()

	normal_id = GetNodeGroupInputIdentifier(gnmod.node_group, "Normal")
	size_id = GetNodeGroupInputIdentifier(gnmod.node_group, "Size")

	obj.location = origin

	gnmod[normal_id][0] = normal_dir[0]
	gnmod[normal_id][1] = normal_dir[1]
	gnmod[normal_id][2] = normal_dir[2]

	gnmod[size_id] = size
	
	return obj

def SetPlaneParameters(plane_obj, origin = None, normal_dir = None, size = None, create_if_not_exist = True):
	if plane_obj == None:
		return
	gnmod = None
	for gnmod in plane_obj.modifiers:
		if gnmod.type == "NODES" and gnmod.node_group.name == "Plane_Gen":
			break

	if (gnmod is None) or (gnmod.type != "NODES") or (gnmod.node_group.name != "Plane_Gen"):
		if not create_if_not_exist:
			return
		gnmod = plane_obj.modifiers.new("Plane", "NODES")

	if origin is not None:
		plane_obj.location = origin

	if normal_dir is not None:
		normal_id = GetNodeGroupInputIdentifier(gnmod.node_group, "Normal")
		gnmod[normal_id][0] = normal_dir[0]
		gnmod[normal_id][1] = normal_dir[1]
		gnmod[normal_id][2] = normal_dir[2]

	if size is not None:
		size_id = GetNodeGroupInputIdentifier(gnmod.node_group, "Size")
		gnmod[size_id] = size

	gnmod.show_on_cage = True
	gnmod.show_on_cage = False

def ConstraintObjsToBoneRotation(objs: list[bpy.types.Object], armature_obj, bone_index) -> bpy.types.Object:
	if armature_obj == None:
		return
	if objs == None or len(objs) == 0:
		return
	if bone_index == None:
		return

	mesh = bpy.data.meshes.new('ANCHOR_OBJ')
	anchor_obj = bpy.data.objects.new('ANCHOR_OBJ', mesh)

	bpy.context.collection.objects.link(anchor_obj)

	ConstraintObjToArmatureBone(anchor_obj, armature_obj, bone_index, True)

	for obj in objs:
		if obj == None:
			continue
		obj.constraints.clear()
		const = obj.constraints.new(type = 'COPY_ROTATION')
		const.target = anchor_obj

	return anchor_obj

def RemoveNonBoneVG(obj:bpy.types.Object, skeleton:bpy.types.Object):
	for vg in obj.vertex_groups:
		if vg.name not in skeleton.data.bones:
			obj.vertex_groups.remove(vg)

def GatherWeights(obj:bpy.types.Object, quantize_bytes = 2, max_blend_entries = 8, prune_empty_vertex_groups = True):
	vertex_groups = obj.vertex_groups
	if prune_empty_vertex_groups:
		vgrp_markers = [[vg.name, -1] for vg in vertex_groups]
	else:
		vgrp_markers = [[vg.name, i] for vg, i in zip(vertex_groups, range(len(vertex_groups)))]
	new_id = 0

	bm = bmesh.new()
	bm.from_mesh(obj.data)
	bm.verts.layers.deform.verify()

	deform = bm.verts.layers.deform.active
	
	weights = []
	_min_weight = 1 / (256 ** quantize_bytes - 2)
	for v in bm.verts:
		g = v[deform]
		
		weights.append([])
		for vg_id, weight in g.items():
			if weight > _min_weight:
				if vgrp_markers[vg_id][1] == -1:
					vgrp_markers[vg_id][1] = new_id
					new_id += 1
				weights[-1].append([vgrp_markers[vg_id][1], weight])
		
		if len(weights[-1]) > max_blend_entries:
			index_list = sorted(range(len(weights[-1])), key=lambda k: weights[-1][k], reverse=True)
			weights[-1] = [weights[-1][i] for i in index_list[:max_blend_entries]]
		
		if len(weights[-1]) == 0:
			weights[-1].append([0, 0])

	vgrp_markers = sorted(vgrp_markers, key=lambda x: x[1])
	vgrp_names = [vg[0] for vg in vgrp_markers if vg[1] != -1]

	bm.free()

	return weights, vgrp_names

def RemapBoneIdToSkeleton(weights, vgrp_names, skeleton:bpy.types.Object):
	remap = {}
	used_indices = set()
	skele_bones = [bone.name for bone in skeleton.data.bones]
	for i, bone in enumerate(vgrp_names):
		if bone in skele_bones:
			remap[i] = skele_bones.index(bone)
			used_indices.add(skele_bones.index(bone))

	for i, w in enumerate(weights):
		for j, v in enumerate(w):
			if v[0] in remap:
				weights[i][j][0] = remap[v[0]]
			else:
				print(f"Bone {v[0]} not found in skeleton.")
				return None, None
			
	return weights, list(used_indices)

def RemapBoneIdToSubset(weights, subset:list, order_subset = True):
	remap = {}
	if order_subset:
		subset = sorted(subset)
	for i, bone_id in enumerate(subset):
		remap[bone_id] = i

	for i, w in enumerate(weights):
		for j, v in enumerate(w):
			if v[0] in remap:
				weights[i][j][0] = remap[v[0]]
			else:
				print(f"Bone {v[0]} not found in subset.")
				return None, None
			
	return weights, subset

def NormalizeAndQuantizeWeights(weights, quantize_bytes = 2):
	max_value = 256 ** quantize_bytes - 1
	for i, w in enumerate(weights):
		_sum = sum([v[1] for v in w])
		weights[i] = [[v[0], int(max_value * v[1] / _sum)] for v in w]
		# Sort by weight
		weights[i] = sorted(weights[i], key=lambda x: x[1], reverse=True)
		# Make sure weights sum up to max_value
		if len(weights[i]) > 1:
			weights[i][0][1] = max_value - sum([v[1] for v in weights[i][1:]])
		else:
			weights[i][0][1] = max_value
	return weights

def RemoveMeshObj(mesh_obj):
	bpy.data.meshes.remove(mesh_obj.data)

def is_plugin_debug_mode() -> bool:
	return bpy.context.scene.sgb_debug_mode == True

def TransferWeightByDistance(target_obj: bpy.types.Object, reference_obj: bpy.types.Object):
	if target_obj == None or reference_obj == None:
		return
	for r_vg in reference_obj.vertex_groups:
		if r_vg.name not in target_obj.vertex_groups:
			target_obj.vertex_groups.new(name = r_vg.name)
	original_active = SetActiveObject(target_obj)
	modifier = target_obj.modifiers.new(name = 'WeightTransfer', type='DATA_TRANSFER')
	modifier.object = reference_obj
	modifier.use_vert_data = True
	modifier.data_types_verts = {'VGROUP_WEIGHTS'}
	bpy.ops.object.modifier_apply(modifier=modifier.name)

	SetActiveObject(original_active)

def get_preferences():
    return bpy.context.preferences.addons["tool_export_mesh"].preferences

def get_preference(prop:str):
	return get_preferences().get(prop)

def get_texconv_path() -> str|None:
	texconv_path = get_preferences().texconv_path
	if texconv_path == '':
		return None
	if not os.path.exists(texconv_path) or not os.path.isfile(texconv_path):
		return None
	return texconv_path

def set_viewport_shading(screen:bpy.types.Screen, **kwargs):
	area:bpy.types.Area = next(area for area in screen.areas if area.type == 'VIEW_3D')
	space:bpy.types.Space = next(space for space in area.spaces if space.type == 'VIEW_3D')

	# setattr
	for key, value in kwargs.items():
		setattr(space.shading, key, value)

def export_report(report_uv_layers: bool):
	target_obj = GetActiveObject()
	selected_objs = GetSelectedObjs(True)

	report = {
		"target_obj": target_obj,
		"selected_objs": selected_objs,
	}
	
	if not report_uv_layers or target_obj == None or target_obj.type != 'MESH':
		return report
	
	first_uv = target_obj.data.uv_layers.active
	second_uv = None
	for _uv_layer in target_obj.data.uv_layers:
		if _uv_layer != first_uv:
			second_uv = _uv_layer

	report["first_uv"] = first_uv
	report["second_uv"] = second_uv

	return report


class ObjectScope:
	def __init__(self, obj:bpy.types.Object, edit_mode = False, edit_mode_select_all_verts = False):
		self.obj = obj
		self.obj_in_view_layer = bpy.context.view_layer.objects.find(obj.name) != -1
		self.original_active = None
		self.original_selected = None
		self.original_mode = None
		self.original_visibility = not self.obj.hide_get(view_layer=bpy.context.view_layer)
		self.edit_mode = edit_mode
		self.edit_mode_select_all_verts = edit_mode_select_all_verts

	def __enter__(self):
		# Add object to view layer if not already in it
		if not self.obj_in_view_layer:
			bpy.context.collection.objects.link(self.obj)
		if not self.original_visibility:
			self.obj.hide_set(False, view_layer=bpy.context.view_layer)

		self.original_active = SetActiveObject(self.obj)
		self.original_selected = SetSelectObjects([])
		self.original_mode = self.obj.mode
		if self.edit_mode:
			if self.obj.mode != 'EDIT':
				bpy.ops.object.mode_set(mode='EDIT')
			bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='VERT')
			if self.edit_mode_select_all_verts:
				bpy.ops.mesh.select_all(action='SELECT')
		return self.obj
	
	def __exit__(self, exc_type, exc_value, traceback):
		if self.original_mode != self.obj.mode:
			bpy.ops.object.mode_set(mode=self.original_mode)
		if self.original_active != self.obj and self.original_active != None:
			SetActiveObject(self.original_active)

		if self.original_selected != None:
			SetSelectObjects(self.original_selected)

		if not self.obj_in_view_layer:
			bpy.context.collection.objects.unlink(self.obj)

		if not self.original_visibility:
			self.obj.hide_set(True, view_layer=bpy.context.view_layer)

def get_obj_scope(obj:bpy.types.Object, edit_mode = False, edit_mode_select_all_verts = False):
	return ObjectScope(obj, edit_mode, edit_mode_select_all_verts)

class ObjectBMeshProxy:
	def __init__(self, obj:bpy.types.Object, triangulation_method: str = 'None', proxy_name = "BMeshProxy"):
		self.target_obj = obj
		self.new_obj = None
		self.proxy_name = proxy_name
		self.bmesh_triangulation = triangulation_method == 'BMesh'
		self.ops_triangulation = triangulation_method == 'Ops'

	def __enter__(self):
		print("Allocating new object.")
		if self.target_obj == None:
			# Create a new object
			self.new_obj = bpy.data.objects.new(self.proxy_name, bpy.data.meshes.new(self.proxy_name))
			return self.new_obj

		self.new_obj = self.target_obj.copy()
		self.new_obj.data = self.target_obj.data.copy()
		if self.bmesh_triangulation:
			bm = bmesh.new()
			bm.from_mesh(self.new_obj.data)
			bmesh.ops.triangulate(bm, faces=bm.faces[:])
			bm.to_mesh(self.new_obj.data)
			bm.free()
		elif self.ops_triangulation:
			with get_obj_scope(self.new_obj, True, True):
				bpy.ops.mesh.quads_convert_to_tris(quad_method='BEAUTY', ngon_method='BEAUTY')

		return self.new_obj
	
	def __exit__(self, exc_type, exc_value, traceback):
		print("Deallocating new object.")
		try:
			bpy.data.meshes.remove(self.new_obj.data)
		except Exception as e:
			print(f"Error: {e}")
			pass
		self.new_obj = None

def get_obj_proxy(obj:bpy.types.Object, triangulation_method = 'None'):
	return ObjectBMeshProxy(obj, triangulation_method)

def get_active_obj_proxy(triangulation_method = 'None'):
	return ObjectBMeshProxy(GetActiveObject(), triangulation_method)

def AverageCustomNormals(obj:bpy.types.Object):
	with get_obj_scope(obj, True, True):
		bpy.ops.mesh.average_normals(average_type='CUSTOM_NORMAL')

def MergeCustomNormals(obj:bpy.types.Object):
	with get_obj_scope(obj, True, True):
		bpy.ops.mesh.merge_normals()

def ApplyShapekey(obj: bpy.types.Object, target_sk_n: str, use_relative = True):
    
    mesh = obj.data
    
    basis_sk = mesh.shape_keys.key_blocks['Basis']

    target_sk = mesh.shape_keys.key_blocks[target_sk_n]

    other_sk = [sk for sk in mesh.shape_keys.key_blocks if sk != sk.relative_key and not sk.mute and sk != target_sk]

    vs = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    ovs = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    
    target_sk.data.foreach_get('co', vs)
    basis_sk.data.foreach_set('co', ovs)

    basis_sk.data.foreach_set('co', vs)

    if use_relative:
        for sk in other_sk:
            rvs = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            sk.data.foreach_get('co', rvs)
            sk.data.foreach_set('co', rvs-ovs+vs)
            
    target_sk.value = 0

def RestoreOperatorDefaults(op):
	for prop in op.bl_rna.properties:
	    if isinstance(prop, bpy.props._PropertyDeferred):
	        setattr(op, prop.keywords['attr'], prop.keywords['default'])
