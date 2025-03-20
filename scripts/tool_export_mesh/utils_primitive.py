import bpy
import numpy as np
import bmesh
import functools
import ctypes

from enum import Enum, unique

import utils_math
import utils_morph_attrs

from utils_common import timer

class AtomicException(Exception):
    pass

class MeshTypeException(Exception):
    pass

class MorphUncalculatedException(Exception):
    pass

class UVIndexException(Exception):
    pass

class UVNotFoundException(Exception):
    pass

class UngatheredException(Exception):
    pass

class Primitive():

    @unique
    class GatheredData(Enum):
        POSITION = 0
        UV = 1
        UV2 = 2
        NORMALS = 3
        COLORS = 4
        TANGENTS = 5
        BITANGENTS = 6
        WEIGHTS = 7
        MORPHNORMALS = 8
        MORPHTANGENTS = 9
        TRIANGLES = 10
        MORPHCOLORS = 11

    class Options():
        def __init__(self):
            self.max_border = 0.0
            self.secondary_uv_layer_index = -1 # May cause different atomic vertices!
            self.gather_weights_data = False
            self.gather_morph_data = False
            self.use_global_positions = False

            self.gather_tangents = True

            self.use_morph_normal_attrs = False # Use morph normal attributes if available
            self.use_morph_color_attrs = True # Use morph target color attributes if available

            # Less frequently changed options
            self.normal_tangent_round_precision = 0.01
            self.atomic_max_number = 65535
            self.weight_cutoff_threshold = 0.0001
            self.max_weights_per_vertex = 8
            self.prune_empty_vertex_groups = True

            self.vertex_group_merge_source:list[str] = []
            self.vertex_group_merge_target:str = ''

            self.vertex_group_ignore:list[str] = []

    def __init__(self, object:bpy.types.Object, options:Options = Options()):
        if object.type != "MESH":
            raise MeshTypeException("Primitive.__init__() expect input 'object' to be a 'MESH' type object. Got: " + object.type)
        self.blender_object:bpy.types.Object = object
        self.blender_mesh:bpy.types.Mesh = object.data
        # Attributes should be per-vertex, used to convert per-loop data to per-vertex data
        self._atomic_attributes = [
            ('vertex_index', np.uint32), 
            ('uv_x', np.float32),
            ('uv_y', np.float32),
            ('uv_x_2', np.float32),
            ('uv_y_2', np.float32),
            ('normal_x', np.float32),
            ('normal_y', np.float32),
            ('normal_z', np.float32),
            ('color_r', np.float32),
            ('color_g', np.float32),
            ('color_b', np.float32),
            ('color_a', np.float32)
        ]
        self.vertex_weights_data = {
            "vertex_weights": [],
            "vertex_group_names": []
        }
        self.shapeKeys = []
        self.triangles = None

        self.atomic_vertices = np.empty(len(self.blender_mesh.loops), dtype=self._atomic_attributes)
        self.atomic_to_loop_id = None # Mapping from atomic vertex id to loop id
        self.loop_id_to_atomic = None # Mapping from loop id to atomic vertex id

        self.gather_color_data = True
        self.color_data_source_index = -1
        self.color_domain = None

        self.options = options

        self.key_blocks:list[bpy.types.ShapeKey] = []

        self.gathered = set()

    @functools.cached_property
    def positions(self):
        return self.raw_positions[self.atomic_vertices['vertex_index']]

    @functools.cached_property
    def uv(self):
        _uv = np.empty((len(self.atomic_vertices), 2), dtype=np.float32) # To ensure row-major order in memory
        _uv[:, 0] = self.atomic_vertices['uv_x']
        _uv[:, 1] = self.atomic_vertices['uv_y']
        return _uv
    
    @functools.cached_property
    def uv_2(self):
        _uv = np.empty((len(self.atomic_vertices), 2), dtype=np.float32)
        _uv[:, 0] = self.atomic_vertices['uv_x_2']
        _uv[:, 1] = self.atomic_vertices['uv_y_2']
        return _uv
    
    @functools.cached_property
    def normals(self):
        _normal = np.empty((len(self.atomic_vertices), 3), dtype=np.float32)
        _normal[:, 0] = self.atomic_vertices['normal_x']
        _normal[:, 1] = self.atomic_vertices['normal_y']
        _normal[:, 2] = self.atomic_vertices['normal_z']
        return _normal

    @functools.cached_property
    def tangents(self):
        return self.raw_tangents[self.atomic_to_loop_id]
    
    @functools.cached_property
    def bitangent_sign(self):
        return self.raw_bitangent_signs[self.atomic_to_loop_id]
    
    @functools.cached_property
    def colors(self):
        _color = np.empty((len(self.atomic_vertices), 4), dtype=np.float32)
        _color[:, 0] = self.atomic_vertices['color_r']
        _color[:, 1] = self.atomic_vertices['color_g']
        _color[:, 2] = self.atomic_vertices['color_b']
        _color[:, 3] = self.atomic_vertices['color_a']
        return _color

    @functools.cached_property
    def morph_positions(self):
        return [raw_morph_positions[self.atomic_vertices['vertex_index']] for raw_morph_positions in self.raw_morph_positions]

    @functools.cached_property
    def morph_normals(self):
        return [raw_morph_normals[self.atomic_to_loop_id] for raw_morph_normals in self.raw_morph_normals]
    
    @functools.cached_property
    def morph_tangents(self):
        return [raw_morph_tangents[self.atomic_to_loop_id] for raw_morph_tangents in self.raw_morph_tangents]
    
    @functools.cached_property
    def morph_position_deltas(self):
        return np.array([raw_morph_position_deltas[self.atomic_vertices['vertex_index']] for raw_morph_position_deltas in self.raw_morph_position_deltas], dtype=np.float32)

    @functools.cached_property
    def morph_target_colors(self):
        return np.array([raw_morph_target_colors[self.atomic_to_loop_id] for raw_morph_target_colors in self.raw_morph_target_colors], dtype=np.float32) * 192

    @functools.cached_property
    def morph_normal_deltas(self):
        return np.array([raw_morph_normal_deltas[self.atomic_to_loop_id] for raw_morph_normal_deltas in self.raw_morph_normal_deltas], dtype=np.float32)
    
    @functools.cached_property
    def morph_tangent_deltas(self):
        return np.array([raw_morph_tangent_deltas[self.atomic_to_loop_id] for raw_morph_tangent_deltas in self.raw_morph_tangent_deltas], dtype=np.float32)

    @functools.cached_property
    def KDTree(self):
        from scipy.spatial import cKDTree
        return cKDTree(self.positions)

    def gather(self):
        if not self.scan_object_for_data():
            print("Primitive.gather() failed to scan object for data")

        self.gather_atomics()

        #self._test_deduplication()

        self.deduplicate_atomics()

        self.gather_positions()

        # Execution order doesn't matter for the following three functions
        if self.options.gather_weights_data:
            self.gather_weights()

        if self.options.gather_morph_data:
            self.gather_morphs()

        self.gather_triangles()
    
    @timer
    def scan_object_for_data(self):
        # Check for UV data
        if not self.blender_mesh.uv_layers.active:
            raise UVNotFoundException("No active UV data found on mesh")
            return False
        
        self.uv_layer = self.blender_mesh.uv_layers.active

        self.second_uv_layer = None
        if self.options.secondary_uv_layer_index != -1:
            if self.options.secondary_uv_layer_index < len(self.blender_mesh.uv_layers):
                self.second_uv_layer = self.blender_mesh.uv_layers[self.options.secondary_uv_layer_index]
                if self.second_uv_layer == self.uv_layer:
                    print("Primitive.scan_object_for_data() Secondary UV layer is the same as primary UV layer!")
            else:
                raise UVIndexException("Primitive.scan_object_for_data() secondary_uv_layer_index out of range")
                self.secondary_uv_layer = None
                self.secondary_uv_layer_index = None

        if self.blender_mesh.color_attributes.render_color_index == -1:
            print("No color data found on mesh")
            self.gather_color_data = False
        else:
            self.color_data_source_index = self.blender_mesh.color_attributes.render_color_index
            self.color_domain = self.blender_mesh.color_attributes[self.color_data_source_index].domain

        self.armature:bpy.types.Object = None

        if self.options.gather_weights_data:
            if not self.blender_object.vertex_groups:
                print("No vertex groups found on object")
                self.options.gather_weights_data = False

            armatures = [m.object for m in self.blender_object.modifiers if m.type == 'ARMATURE' and m.object is not None]

            if not armatures:
                print("No armature modifier found on object")
                self.options.gather_weights_data = False
                self.armature = None
            elif len(armatures) > 1:
                print("Multiple armature modifiers found on object")
                self.options.gather_weights_data = False
                self.armature = armatures[0]
            else:
                self.armature = armatures[0]

        if self.options.gather_morph_data:
            if self.blender_mesh.shape_keys:
                for key_block in self.blender_mesh.shape_keys.key_blocks:
                    if key_block != key_block.relative_key and not key_block.mute:
                        self.key_blocks.append(key_block)
                if not self.key_blocks:
                    print("No exportable shape keys found on mesh")
                    self.options.gather_morph_data = False
            else:
                print("No shape keys found on mesh")
                self.options.gather_morph_data = False

        if self.options.vertex_group_merge_target != '' and self.options.vertex_group_merge_source:
            if not any([vg.name in self.blender_object.vertex_groups for vg in self.options.vertex_group_merge_source]):
                print("All source vertex groups not found on object")
                self.options.vertex_group_merge_source = []
                self.options.vertex_group_merge_target = ''
            elif self.options.vertex_group_merge_target not in self.blender_object.vertex_groups:
                print("Target vertex group not found on object")
                self.options.vertex_group_merge_source = []
                self.options.vertex_group_merge_target = ''
            else:
                source_indices = [self.blender_object.vertex_groups.find(vg_name) for vg_name in self.options.vertex_group_merge_source]
                source_indices = np.array([i for i in source_indices if i != -1])
                target_index = self.blender_object.vertex_groups.find(self.options.vertex_group_merge_target)
                self.vertex_group_indices_mapping = np.array(range(len(self.blender_object.vertex_groups)))
                self.vertex_group_indices_mapping[source_indices] = target_index                           

        self.vertex_group_ignore_indices = set()
        if self.options.vertex_group_ignore:
            ignore_indices = [self.blender_object.vertex_groups.find(vg_name) for vg_name in self.options.vertex_group_ignore]
            self.vertex_group_ignore_indices = set([i for i in ignore_indices if i != -1])

        return True
    
    @timer
    def gather_atomics(self):
        # Gather vertex index data
        self.blender_mesh.loops.foreach_get('vertex_index', self.atomic_vertices['vertex_index'])

        # Gather UV data
        _temp_arr = np.empty(len(self.blender_mesh.loops) * 2, dtype = np.float32)
        self.uv_layer.data.foreach_get('uv', _temp_arr)
        uvs = np.round(_temp_arr, 3).reshape(-1, 2).T
        # u, v -> u, 1-v
        uvs[1] = 1 - uvs[1]
        self.atomic_vertices['uv_x'] = uvs[0]
        self.atomic_vertices['uv_y'] = uvs[1]
        self.gathered.add(Primitive.GatheredData.UV)

        # Gather secondary UV data if available
        if self.options.secondary_uv_layer_index != -1 and self.second_uv_layer:
            _temp_arr = np.empty(len(self.blender_mesh.loops) * 2, dtype = np.float32)
            self.second_uv_layer.data.foreach_get('uv', _temp_arr)
            uvs = np.round(_temp_arr, 3).reshape(-1, 2).T
            # u, v -> u, 1-v
            uvs[1] = 1 - uvs[1]
            self.atomic_vertices['uv_x_2'] = uvs[0]
            self.atomic_vertices['uv_y_2'] = uvs[1]
            self.gathered.add(Primitive.GatheredData.UV2)
        else:
            self.atomic_vertices['uv_x_2'] = np.zeros(len(self.blender_mesh.loops), dtype=np.float32)
            self.atomic_vertices['uv_y_2'] = np.zeros(len(self.blender_mesh.loops), dtype=np.float32)

        # Gather normal data
        self._calculate_normals()
        normals = self.raw_normals.reshape(-1, 3).T
        self.atomic_vertices['normal_x'] = normals[0]
        self.atomic_vertices['normal_y'] = normals[1]
        self.atomic_vertices['normal_z'] = normals[2]

        # Gather tangent data
        # Gather bitangent sign data
        if self.options.gather_tangents:
            self._calculate_tangents()

        # Gather color data
        if self.gather_color_data and self.color_data_source_index != -1 and self.color_domain:
            if self.color_domain == 'POINT':
                colors = np.empty(len(self.blender_mesh.vertices) * 4, dtype = np.float32)
            elif self.color_domain == 'CORNER':
                colors = np.empty(len(self.blender_mesh.loops) * 4, dtype = np.float32)

            self.blender_mesh.color_attributes[self.color_data_source_index].data.foreach_get('color', colors)
            
            if self.color_domain == 'POINT':
                colors = colors.reshape(-1, 4)
                colors = colors[self.atomic_vertices['vertex_index']].T
            elif self.color_domain == 'CORNER':
                colors = colors.reshape(-1, 4).T

            self.atomic_vertices['color_r'] = colors[0]
            self.atomic_vertices['color_g'] = colors[1]
            self.atomic_vertices['color_b'] = colors[2]
            self.atomic_vertices['color_a'] = colors[3]
            self.gathered.add(Primitive.GatheredData.COLORS)
        else:
            self.atomic_vertices['color_r'] = np.zeros(len(self.blender_mesh.loops), dtype=np.float32)
            self.atomic_vertices['color_g'] = np.zeros(len(self.blender_mesh.loops), dtype=np.float32)
            self.atomic_vertices['color_b'] = np.zeros(len(self.blender_mesh.loops), dtype=np.float32)
            self.atomic_vertices['color_a'] = np.zeros(len(self.blender_mesh.loops), dtype=np.float32)

    @timer
    def deduplicate_atomics(self, raise_exception = True):
        temp_atomic_vertices, temp_atomic_to_loop_id, loop_id_to_temp_atomic = np.unique(self.atomic_vertices, return_index=True, return_inverse=True)

        # Refine normal deduplication
        self.refine_normal_deduplication(temp_atomic_vertices)

        self.atomic_vertices, atomic_to_temp_atomic, temp_atomic_to_atomic = np.unique(temp_atomic_vertices, return_index=True, return_inverse=True)

        self.atomic_to_loop_id = temp_atomic_to_loop_id[atomic_to_temp_atomic]
        self.loop_id_to_atomic = temp_atomic_to_atomic[loop_id_to_temp_atomic]

        print("Final vertices count: " + str(len(self.atomic_vertices)))

        if len(self.atomic_vertices) > self.options.atomic_max_number:
            if raise_exception:
                raise AtomicException("Primitive.deduplicate_atomics() deduplicated atomic vertices exceed atomic_max_number")
            else:
                return False
            
        return True

    @timer
    def _test_deduplication(self, raise_exception = True):
        print("Initial loop vertices count: " + str(len(self.atomic_vertices)))

        _test_uv = np.unique(self.atomic_vertices[['vertex_index', 'uv_x', 'uv_y', 'uv_x_2', 'uv_y_2']])
        print("Final vertices count by UV: " + str(len(_test_uv)))

        _test_normals = np.unique(self.atomic_vertices[['vertex_index', 'normal_x', 'normal_y', 'normal_z']])
        print("Final vertices count by normals: " + str(len(_test_normals)))

        _test_colors = np.unique(self.atomic_vertices[['vertex_index', 'color_r', 'color_g', 'color_b', 'color_a']])
        print("Final vertices count by colors: " + str(len(_test_colors)))

        #first_elements = _test_normals['vertex_index']
        #unique_elements, counts = np.unique(first_elements, return_counts=True)

        #repeated_vertices = _test_normals[np.isin(first_elements, unique_elements[counts > 1])]
        #for vertex_index in repeated_vertices['vertex_index']:
        #    print(self.atomic_vertices[self.atomic_vertices['vertex_index']==vertex_index])

        return True
    
    @timer
    def refine_normal_deduplication(self, atomic_verts, threshold = 0.02):
        first_elements = atomic_verts['vertex_index']
        unique_elements, counts = np.unique(first_elements, return_counts=True)

        repeated_vertices = atomic_verts[np.isin(first_elements, unique_elements[counts > 1])]

        normals = np.vstack((atomic_verts['normal_x'], atomic_verts['normal_y'], atomic_verts['normal_z'])).T

        for vertex_index in repeated_vertices['vertex_index']:
            vert_normal_group = normals[atomic_verts['vertex_index'] == vertex_index]
            # Get max, min value for each field
            max = np.max(vert_normal_group, axis=0)
            min = np.min(vert_normal_group, axis=0)

            if np.max(utils_math.min_max_dist(vert_normal_group)) < threshold:
                center = np.mean(vert_normal_group, axis=0)
                
                # Replace all normals with the center
                atomic_verts['normal_x'][atomic_verts['vertex_index'] == vertex_index] = center[0]
                atomic_verts['normal_y'][atomic_verts['vertex_index'] == vertex_index] = center[1]
                atomic_verts['normal_z'][atomic_verts['vertex_index'] == vertex_index] = center[2]
    @timer
    def gather_positions(self):
        self.raw_positions = np.empty(len(self.blender_mesh.vertices) * 3, dtype=np.float32)
        self.blender_mesh.vertices.foreach_get('co', self.raw_positions)
        self.raw_positions = self.raw_positions.reshape(-1, 3)
        self._post_vertex_transform(self.raw_positions)

        self.gathered.add(Primitive.GatheredData.POSITION)

        self.raw_morph_positions = []
        self.raw_morph_position_deltas = []
        for key_block in self.key_blocks:
            vs = np.empty(len(self.blender_mesh.vertices) * 3, dtype=np.float32)
            key_block.data.foreach_get('co', vs)
            vs = vs.reshape(len(self.blender_mesh.vertices), 3)

            self._post_vertex_transform(vs)

            self.raw_morph_positions.append(vs)
            self.raw_morph_position_deltas.append(vs - self.raw_positions)

    @timer
    def gather_weights(self):
        vertex_groups = self.blender_object.vertex_groups
        if self.options.prune_empty_vertex_groups:
            vgrp_markers = [[vg.name, -1] for vg in vertex_groups]
        else:
            vgrp_markers = [[vg.name, i] for vg, i in zip(vertex_groups, range(len(vertex_groups)))]
        new_id = 0

        bm = bmesh.new()
        bm.from_mesh(self.blender_mesh)
        bm.verts.layers.deform.verify()
        deform = bm.verts.layers.deform.active
        weight_data = []
        _min_weight = self.options.weight_cutoff_threshold

        if self.options.vertex_group_merge_source and self.options.vertex_group_merge_target != '':
            for v in bm.verts:
                g = v[deform]
                
                vertex_weight_data = []
                for bone_id, weight in g.items():
                    if bone_id in self.vertex_group_ignore_indices:
                        continue
                    bone_id = self.vertex_group_indices_mapping[bone_id]
                    if weight > _min_weight:
                        if vgrp_markers[bone_id][1] == -1:
                            vgrp_markers[bone_id][1] = new_id
                            new_id += 1
                        vertex_weight_data.append([vgrp_markers[bone_id][1], weight])

                # Deduplicate the list by the first element
                for i in range(len(vertex_weight_data)):
                    for j in range(i+1, len(vertex_weight_data)):
                        if vertex_weight_data[i][0] == vertex_weight_data[j][0]:
                            vertex_weight_data[i][1] += vertex_weight_data[j][1]
                            vertex_weight_data[j][1] = 0

                # Remove the elements with 0 weight
                vertex_weight_data = [x for x in vertex_weight_data if x[1] != 0]
                
                if len(vertex_weight_data) > self.options.max_weights_per_vertex:
                    vertex_weight_data = sorted(vertex_weight_data, key=lambda x: x[1], reverse=True)[:self.options.max_weights_per_vertex]
                elif len(vertex_weight_data) == 0:
                    vertex_weight_data.append([0, 0])

                weight_data.append(vertex_weight_data)

        else:
            for v in bm.verts:
                g = v[deform]
                
                vertex_weight_data = []
                for bone_id, weight in g.items():
                    if bone_id in self.vertex_group_ignore_indices:
                        continue
                    if weight > _min_weight:
                        if vgrp_markers[bone_id][1] == -1:
                            vgrp_markers[bone_id][1] = new_id
                            new_id += 1
                        vertex_weight_data.append([vgrp_markers[bone_id][1], weight])
                
                if len(vertex_weight_data) > self.options.max_weights_per_vertex:
                    vertex_weight_data = sorted(vertex_weight_data, key=lambda x: x[1], reverse=True)[:self.options.max_weights_per_vertex]
                elif len(vertex_weight_data) == 0:
                    vertex_weight_data.append([0, 0])

                weight_data.append(vertex_weight_data)

        self.vertex_weights_data["vertex_weights"] = [weight_data[i] for i in self.atomic_vertices['vertex_index']]
        vgrp_markers = sorted(vgrp_markers, key=lambda x: x[1])
        self.vertex_weights_data['vertex_group_names'] = [vg[0] for vg in vgrp_markers if vg[1] != -1]

        bm.free()
        
        self.gathered.add(Primitive.GatheredData.WEIGHTS)

        print("Final vertex weights count: " + str(len(self.vertex_weights_data["vertex_weights"])))

    @timer
    def gather_morphs(self):
        self.shapeKeys = [key_block.name for key_block in self.key_blocks]

        self.raw_morph_target_colors = []

        morph_target_colors = utils_morph_attrs.MorphTargetColors()

        key_blocks = self.key_blocks if self.options.gather_morph_data else []

        for key_block in key_blocks:
            col_attr = None

            if self.options.use_morph_color_attrs:
                col_attr = morph_target_colors.validate(self.blender_mesh, key_block.name, remove_invalid=False, create_if_invalid=False)

            if col_attr is None:
                raw_morph_target_colors = np.ones((len(self.blender_mesh.loops), 3), dtype=np.float32)
            else:
                print(f"Primitive.gather_morphs() found valid color attribute for shape key: {key_block.name}")
                raw_morph_target_colors = morph_target_colors.gather(self.blender_mesh, key_block.name).reshape(-1, 4)[:, :3]
                
            self.raw_morph_target_colors.append(raw_morph_target_colors)

        self.gathered.add(Primitive.GatheredData.MORPHCOLORS)

    @timer
    def gather_triangles(self):
        self.blender_mesh.calc_loop_triangles()
        self.triangles = np.empty(len(self.blender_mesh.loop_triangles) * 3, dtype=np.uint32)
        self.blender_mesh.loop_triangles.foreach_get('loops', self.triangles)

        # For each loop id in triangles, replace it with the corresponding atomic vertex id
        self.triangles = self.loop_id_to_atomic[self.triangles]

        self.gathered.add(Primitive.GatheredData.TRIANGLES)

        print("Final triangles count: " + str(len(self.triangles)))

    def _calculate_normals(self):
        '''
            Inspired from glTF 2.0 exporter for Blender
        '''
        key_blocks = self.key_blocks if self.options.gather_morph_data else []
        if key_blocks:
            self.raw_normals = raw_corner_normals = key_blocks[0].relative_key.normals_split_get()
            self.raw_normals = np.array(self.raw_normals, dtype=np.float32)
        else:
            self.raw_normals = np.empty(len(self.blender_mesh.loops) * 3, dtype=np.float32)
            self.blender_mesh.corner_normals.foreach_get('vector', self.raw_normals)
        
        self.raw_normals = self.raw_normals.reshape(-1, 3)

        self.raw_normals = utils_math.prec_round(self.raw_normals, self.options.normal_tangent_round_precision)

        # Handle degenrated normals
        is_zero = ~self.raw_normals.any(axis=1)
        self.raw_normals[is_zero, 2] = 1

        self._post_normal_transform(self.raw_normals)
        self.gathered.add(Primitive.GatheredData.NORMALS)

        self.raw_morph_normals = []
        self.raw_morph_normal_deltas = []
        if self.options.gather_morph_data:

            for key_block in key_blocks:
                
                # If attribute is found, use it.
                attr:bpy.types.Attribute|None = None

                if self.options.use_morph_normal_attrs:
                    attr = utils_morph_attrs.MorphNormals().validate(self.blender_mesh, key_block.name, remove_invalid=False, create_if_invalid=False)

                if attr is not None:
                    raw_morph_normal_deltas = utils_morph_attrs.MorphNormals().gather(self.blender_mesh, key_block.name)

                    # Sum normals deltas + raw corner normals of the basis
                    raw_morph_normals = np.array(raw_corner_normals + raw_morph_normal_deltas, dtype=np.float32)
                else:
                    raw_morph_normals = np.array(key_block.normals_split_get(), dtype=np.float32)

                raw_morph_normals = raw_morph_normals.reshape(len(self.blender_mesh.loops), 3)
                raw_morph_normals = utils_math.prec_round(raw_morph_normals, self.options.normal_tangent_round_precision)

                # Handle degenrated normals
                is_zero = ~raw_morph_normals.any(axis=1)
                raw_morph_normals[is_zero, 2] = 1

                self._post_normal_transform(raw_morph_normals)

                self.raw_morph_normals.append(raw_morph_normals)
                
                # For DirectX compression format
                
                # Raw morph normal deltas are already got using normal attributes,
                # no need to get it twice.
                if attr is not None:
                    raw_morph_normal_deltas = np.reshape(raw_morph_normal_deltas, (-1, 3))
                else:
                    raw_morph_normal_deltas = utils_math.bounded_vector_substraction(self.raw_normals, raw_morph_normals)

                self.raw_morph_normal_deltas.append(raw_morph_normal_deltas)

            self.gathered.add(Primitive.GatheredData.MORPHNORMALS)

    def _calculate_tangents(self):
        self.blender_mesh.calc_tangents()
        self.raw_tangents = np.empty(len(self.blender_mesh.loops) * 3, dtype = np.float32)
        self.blender_mesh.loops.foreach_get('tangent', self.raw_tangents)
        self.raw_tangents = self.raw_tangents.reshape(len(self.blender_mesh.loops), 3)

        self.raw_tangents = utils_math.prec_round(self.raw_tangents, self.options.normal_tangent_round_precision)

        self._post_tangent_transform(self.raw_tangents)

        self.gathered.add(Primitive.GatheredData.TANGENTS)
        
        _temp_arr = np.empty(len(self.blender_mesh.loops), dtype = np.int32)
        self.blender_mesh.loops.foreach_get('bitangent_sign', _temp_arr)
        self.raw_bitangent_signs = _temp_arr
        self._post_bitangent_transform()

        self.gathered.add(Primitive.GatheredData.BITANGENTS)

        # Should be the same as implementation in glTF 2.0 exporter for Blender, but a lot faster (30+ times faster)
        # Calculate morph tangents from morph normals, basis normals and basis tangents (64k verts 77 SK, 160 ms)
        self.raw_morph_tangents = []
        self.raw_morph_tangent_deltas = []
        if self.options.gather_morph_data:
            for raw_morph_normals in self.raw_morph_normals:
                batch_rot = utils_math.batch_rotation_matrices(self.raw_normals, raw_morph_normals)

                raw_morph_tangents = np.einsum('ijk,ik->ij', batch_rot, self.raw_tangents)

                self.raw_morph_tangents.append(raw_morph_tangents)

                # For DirectX compression format
                morph_tangent_deltas = self.raw_bitangent_signs[:, np.newaxis] * utils_math.bounded_vector_substraction(self.raw_tangents, raw_morph_tangents)

                self.raw_morph_tangent_deltas.append(morph_tangent_deltas)

            self.gathered.add(Primitive.GatheredData.MORPHTANGENTS)

    def _post_vertex_transform(self, vertices:np.ndarray) -> None:
        # Potentially rotations and flips
        if self.armature or self.options.use_global_positions:
            matrix = self.blender_object.matrix_world
            vertices = utils_math.apply_mat_to_all(matrix, vertices)

    def _post_normal_transform(self, vectors:np.ndarray):
        # Potentially rotations and flips
        if self.armature:
            apply_matrix = self.armature.matrix_world.inverted_safe() @ self.blender_object.matrix_world
            apply_matrix = apply_matrix.to_3x3().inverted_safe().transposed()
            normal_transform = self.armature.matrix_world.to_3x3() @ apply_matrix
            vectors = utils_math.apply_mat_to_all(normal_transform, vectors)
        elif self.options.use_global_positions:
            apply_matrix = self.blender_object.matrix_world
            normal_transform = apply_matrix.to_3x3().inverted_safe().transposed()
            vectors = utils_math.apply_mat_to_all(normal_transform, vectors)
        utils_math.NormalizeRows(vectors)

    def _post_tangent_transform(self, vectors:np.ndarray):
        # Potentially rotations and flips
        if self.armature:
            apply_matrix = self.armature.matrix_world.inverted_safe() @ self.blender_object.matrix_world
            tangent_transform = apply_matrix.to_quaternion().to_matrix()
            vectors = utils_math.apply_mat_to_all(tangent_transform, vectors)
        elif self.options.use_global_positions:
            apply_matrix = self.blender_object.matrix_world
            tangent_transform = apply_matrix.to_quaternion().to_matrix()
            vectors = utils_math.apply_mat_to_all(tangent_transform, vectors)
        utils_math.NormalizeRows(vectors)

    def _post_bitangent_transform(self):
        if self.armature:
            apply_matrix = self.armature.matrix_world.inverted_safe() @ self.blender_object.matrix_world
            tangent_transform = apply_matrix.to_quaternion().to_matrix()
            flipped = tangent_transform.determinant() < 0
            if flipped:
                self.raw_bitangent_signs *= -1
        elif self.options.use_global_positions:
            apply_matrix = self.blender_object.matrix_world
            tangent_transform = apply_matrix.to_quaternion().to_matrix()
            flipped = tangent_transform.determinant() < 0
            if flipped:
                self.raw_bitangent_signs *= -1

    def post_change_normals(self, new_normals, mask):
        old_normals = self.normals[mask]
        self.normals[mask] = new_normals

        # Correct tangent vectors
        batch_rot = utils_math.batch_rotation_matrices(old_normals, new_normals)
        self.tangents[mask] = np.einsum('ijk,ik->ij', batch_rot, self.tangents[mask])
    
    def post_change_morph_normals(self, new_morph_normals, morph_index, mask):
        old_normals = self.morph_normals[morph_index][mask]
        self.morph_normals[morph_index][mask] = new_morph_normals
        self.morph_normal_deltas[morph_index][mask] = utils_math.bounded_vector_substraction(self.normals[mask], new_morph_normals)
        # Correct tangent vecto
        batch_rot = utils_math.batch_rotation_matrices(old_normals, new_morph_normals)
        self.morph_tangents[morph_index][mask] = np.einsum('ijk,ik->ij', batch_rot, self.morph_tangents[morph_index][mask])
        self.morph_tangent_deltas[morph_index][mask] = self.bitangent_sign[mask, np.newaxis] * utils_math.bounded_vector_substraction(self.tangents[mask], self.morph_tangents[morph_index][mask])

    @timer
    def to_mesh_json_dict(self):
        # Throw exception if TANGENTS or BITANGENTS not in gathered data
        if Primitive.GatheredData.TANGENTS not in self.gathered or Primitive.GatheredData.BITANGENTS not in self.gathered:
            raise UngatheredException("Primitive.to_mesh_json_dict() called without gather_tangents option set to True")
            return None
        data = {
            "max_border": self.options.max_border,
            "num_verts": len(self.atomic_vertices),
            "positions_raw": self.positions.flatten().tolist(),
            "num_indices": len(self.triangles),
            "vertex_indices_raw": self.triangles.tolist(),
            "normals": self.normals.tolist(),
            "uv_coords": self.uv.tolist(),
            "vertex_color": self.colors.tolist() if self.gather_color_data else [],
            "vertex_group_names": self.vertex_weights_data["vertex_group_names"],
            "vertex_weights": self.vertex_weights_data["vertex_weights"],
            "smooth_group": [],
            "tangents": [list(t) + [3 if f < 0 else 0] for t, f in zip(self.tangents.tolist(), self.bitangent_sign.tolist())],
        }
        if self.options.secondary_uv_layer_index:
            data["uv_coords_2"] = self.uv_2.tolist()

        return data

    @timer
    def to_mesh_numpy_dict(self):
        if Primitive.GatheredData.TANGENTS not in self.gathered or Primitive.GatheredData.BITANGENTS not in self.gathered:
            raise UngatheredException("Primitive.to_mesh_numpy_dict() called without gather_tangents option set to True")
            return None


        data_matrices = {
            "positions_raw": self.positions,# np.float32
            "vertex_indices_raw": self.triangles, # np.int64
            "normals": self.normals, # np.float32
            "uv_coords": self.uv, # np.float32
            "vertex_color": self.colors if self.gather_color_data else None, # np.float32
            "tangents": self.tangents, # np.float32
            "bitangent_signs": self.bitangent_sign, # np.int32
            "uv_coords_2": self.uv_2 if self.options.secondary_uv_layer_index != -1 else None # np.float32
        }
        data = {
            "max_border": self.options.max_border,
            "num_verts": len(self.atomic_vertices),
            "num_indices": len(self.triangles),
            "vertex_group_names": self.vertex_weights_data["vertex_group_names"],
            "vertex_weights": self.vertex_weights_data["vertex_weights"],
            "ptr_positions": ctypes.addressof(self.positions.ctypes.data_as(ctypes.POINTER(ctypes.c_float)).contents), # np.float32
            "ptr_indices": ctypes.addressof(self.triangles.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)).contents), # np.int64
            "ptr_normals": ctypes.addressof(self.normals.ctypes.data_as(ctypes.POINTER(ctypes.c_float)).contents), # np.float32
            "ptr_uv1": ctypes.addressof(self.uv.ctypes.data_as(ctypes.POINTER(ctypes.c_float)).contents), # np.float32
            "ptr_uv2": ctypes.addressof(self.uv_2.ctypes.data_as(ctypes.POINTER(ctypes.c_float)).contents) if self.options.secondary_uv_layer_index != -1 else 0, # np.float32
            "ptr_color": ctypes.addressof(self.colors.ctypes.data_as(ctypes.POINTER(ctypes.c_float)).contents) if self.gather_color_data else 0, # np.float32
            "ptr_tangents": ctypes.addressof(self.tangents.ctypes.data_as(ctypes.POINTER(ctypes.c_float)).contents), # np.float32
            "ptr_bitangent_signs": ctypes.addressof(self.bitangent_sign.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)).contents), # np.int32
        }

        print("ptr_positions", data["ptr_positions"])

        return data_matrices, data
    
    @timer
    def to_morph_json_dict(self):
        if not self.options.gather_morph_data:
            raise MorphUncalculatedException("Primitive.to_morph_json_dict() called without gather_morph_data option set to True")
            return None
        data = {
            "numVertices": len(self.atomic_vertices),
            "shapeKeys": self.shapeKeys,
            "deltaPositions": self.morph_position_deltas.tolist(),
            "targetColors": self.morph_target_colors.tolist(),
            "deltaNormals": self.morph_normal_deltas.tolist(),
            "deltaTangents": self.morph_tangent_deltas.tolist() if self.options.gather_tangents else []
        }
        return data
    
    @timer
    def to_morph_numpy_dict(self):
        if not self.options.gather_morph_data:
            raise MorphUncalculatedException("Primitive.to_morph_numpy_dict() called without gather_morph_data option set to True")
            return None
        if Primitive.GatheredData.MORPHTANGENTS not in self.gathered:
            raise UngatheredException("Primitive.to_morph_numpy_dict() called without gather_tangents option set to True")
            return None

        data = {
            "numVertices": len(self.atomic_vertices),
            "shapeKeys": self.shapeKeys,
            "deltaPositions": self.morph_position_deltas,
            "targetColors": self.morph_target_colors,
            "deltaNormals": self.morph_normal_deltas,
            "deltaTangents": self.morph_tangent_deltas,
        }
        return data

def CheckForPrimitive(blender_object:bpy.types.Object, gather_tangents = True, gather_morphs = False):
    # Mesh type
    if blender_object.type != 'MESH':
        return False, "Object is not a mesh"
    
    # Check if triangulated
    if gather_tangents and len(blender_object.data.loop_triangles) != len(blender_object.data.loops) // 3:
        return False, "Object is not triangulated"
    
    # Check if has UV
    if not blender_object.data.uv_layers.active:
        return False, "Object has no active UV data"
    
    if gather_morphs:
        if blender_object.data.shape_keys:
            has_exportable_shape_keys = False
            for key_block in blender_object.data.shape_keys.key_blocks:
                if key_block != key_block.relative_key and not key_block.mute:
                    has_exportable_shape_keys = True
                    break
            if not has_exportable_shape_keys:
                return False, "No exportable shape keys found on mesh"
        else:
            return False, "No shape keys found on mesh"

    return True, ""

@timer
def SnapPositions(src_primitive:Primitive, tar_primitive:Primitive, copy_range = 0.005, lerp_coeff = 1.0):
    '''
        src_primitive: Source Shape
        tar_primitive: Target Shape
        copy_range: Maximum distance to snap
        lerp_coeff: Coefficient for linear interpolation
    '''
    if Primitive.GatheredData.POSITION not in src_primitive.gathered or Primitive.GatheredData.POSITION not in tar_primitive.gathered:
        raise UngatheredException("SnapPositions() called with ungathered positions")

    from scipy.spatial import cKDTree
    
    tar_kdtree: cKDTree = tar_primitive.KDTree

    dists, indices = tar_kdtree.query(src_primitive.positions, k=1)
    mask = dists < copy_range
    indices = indices[mask]

    print("Snapped verts: ", len(indices))

    src_primitive.positions[mask] = tar_primitive.positions[indices] * lerp_coeff + src_primitive.positions[mask] * (1 - lerp_coeff)

@timer
def CopyNormalsAtSeam(src_primitive:Primitive, tar_primitive:Primitive, copy_range = 0.005, lerp_coeff = 1.0):
    if Primitive.GatheredData.POSITION not in src_primitive.gathered or Primitive.GatheredData.POSITION not in tar_primitive.gathered:
        raise UngatheredException("CopyNormalsAtSeam() called with ungathered positions")
    if Primitive.GatheredData.NORMALS not in src_primitive.gathered or Primitive.GatheredData.NORMALS not in tar_primitive.gathered:
        raise UngatheredException("CopyNormalsAtSeam() called with ungathered normals")

    from scipy.spatial import cKDTree

    tar_kdtree: cKDTree = tar_primitive.KDTree

    dists, indices = tar_kdtree.query(src_primitive.positions, k=1)
    mask = dists < copy_range
    indices = indices[mask]

    print("Snapped verts: ", len(indices))

    src_primitive.post_change_normals(tar_primitive.normals[indices] * lerp_coeff + src_primitive.normals[mask] * (1 - lerp_coeff), mask)

@timer
def CopyMorphNormalsAtSeam(src_primitive:Primitive, tar_primitive:Primitive, copy_range = 0.005, snap_delta_positions = False, lerp_coeff = 1.0, lerp_coeff_delta_pos = 1.0):
    if Primitive.GatheredData.POSITION not in src_primitive.gathered or Primitive.GatheredData.POSITION not in tar_primitive.gathered:
        raise UngatheredException("CopyMorphNormalsAtSeam() called with ungathered positions")
    if Primitive.GatheredData.MORPHNORMALS not in src_primitive.gathered or Primitive.GatheredData.MORPHNORMALS not in tar_primitive.gathered:
        raise UngatheredException("CopyMorphNormalsAtSeam() called with ungathered morph data")
    
    from scipy.spatial import cKDTree

    tar_kdtree: cKDTree = tar_primitive.KDTree

    dists, indices = tar_kdtree.query(src_primitive.positions, k=1)
    mask = dists < copy_range
    indices = indices[mask]

    print("Snapped verts: ", len(indices))

    src_morphs = src_primitive.shapeKeys
    tar_morphs = tar_primitive.shapeKeys

    common_morphs = list(set(src_morphs) & set(tar_morphs))

    print("Common morphs: ", common_morphs)

    src_indices = [src_morphs.index(morph) for morph in common_morphs]
    tar_indices = [tar_morphs.index(morph) for morph in common_morphs]

    for s_m_id, t_m_id in zip(src_indices, tar_indices):
        src_primitive.post_change_morph_normals(tar_primitive.morph_normals[t_m_id][indices] * lerp_coeff + src_primitive.morph_normals[s_m_id][mask] * (1 - lerp_coeff), s_m_id, mask)
        if snap_delta_positions:
            src_primitive.morph_position_deltas[s_m_id][mask] = tar_primitive.morph_position_deltas[t_m_id][indices] * lerp_coeff_delta_pos + src_primitive.morph_position_deltas[s_m_id][mask] * (1 - lerp_coeff_delta_pos)

if __name__ == "__main__":
    import time
    
    start = time.time()
    obj = bpy.context.active_object
    primitive = Primitive(obj)
    primitive.gather()
    print("Time total: ", time.time() - start)