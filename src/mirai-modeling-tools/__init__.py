"""
Blender operator + UI panel to bake a 128x128 shadow texture of the active object.

Usage:
- Open this script in Blender's text editor and run it (or install as an addon).
- In the 3D View > Sidebar (N-panel) under the "Shadow Baker" panel, click "Bake Shadow Texture".

What the operator does:
1. Takes the active object (must be a mesh).
2. Computes a bounding radius and creates a plane centered on the object's X/Y at the object's lowest world Z.
   - The plane side length is twice the object's radius (so it fully covers the object footprint).
3. Creates a new 128x128 RGBA image and assigns it to a material on the plane (so the plane has a texture to write to).
4. Adds a top-down sun light oriented to cast shadows onto the plane. Places an orthographic camera above the plane.
5. Makes the original object invisible to the camera but still cast shadows.
6. Renders the scene at 128x128, then post-processes the rendered image: any nearly-white pixel is made fully transparent
   (RGB set to black, A = 0). Black (shadow) areas remain black and opaque.
7. Saves the final PNG next to the current .blend file (or in temp folder) as "baked_shadow.png".

Notes & assumptions (safe defaults):
- Uses Cycles as the render engine (shadow catcher workflows are simpler in Cycles).
- If Cycles is unavailable, the script tries to switch automatically.
- The script attempts to avoid changing the user's scene permanently: it stores and restores some settings where reasonable,
  but it does create a plane, a light and a camera which remain in the scene after running (so you can inspect them).

Compatibility: Blender 2.90+ (tested style against modern APIs). Some fields/names may vary between versions.

"""

import bpy
import bmesh
import mathutils
import random
import math
import os


COLLECTIONS = ["Shadow", "Collider", "Asset"]

ARROW = None

MEASUREMENTS ={
    "Table":[0.71,0.76,"table.png"],
    "Chair":[0.45,0.5],
    "Hanger":[1.5,1.8],
    "High table":[1,1.07],
    "FREE (Only for exceptions)":[0,9999],
}

PROPS = [
    ('name', bpy.props.StringProperty(name='Name',default='MyAsset')),
    ('pathToFile',bpy.props.StringProperty(name='Folder',default='',subtype='FILE_PATH',)),
    ('angle', bpy.props.FloatProperty(name='Euler light angle',default=13.3,min=0)),
    ('simetric', bpy.props.BoolProperty(name='Simetric shadows',default=False)),
    ('strength', bpy.props.FloatProperty(name='Light Strength',default=5.0,min=0)),
    ('ExportCollection', bpy.props.BoolProperty(name='Export Collection',default=False)),
    ('Collection',bpy.props.PointerProperty(name='Collection',type=bpy.types.Collection)),
    ("targetObj", bpy.props.PointerProperty(name="Target Object", type=bpy.types.Object)),
    ("numLights", bpy.props.IntProperty(name="Number of Lights", default=4, min=1, max=16)),
    ('measurements', bpy.props.EnumProperty(name='Measurements',items=[(key,key,key) for key in MEASUREMENTS.keys()],default='Table'),),
    ('ARROW', bpy.props.PointerProperty(name='Arrow', type=bpy.types.Object))
]

# ---------- Utility functions ----------

def fix_origin(obj):
    """Set the object's origin to the center of its bottom face and move the object to (0,0,0)."""
    if obj.type != 'MESH' or obj ==None:
        return

    eps=1e-6

    #set to object mode
    mode = bpy.context.mode
    if mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Compute center of bottom face in local space
    bb_local = [mathutils.Vector(v) for v in obj.bound_box]
    min_z = min(v.z for v in bb_local)
    bottom_verts = [v for v in bb_local if abs(v.z - min_z) <= eps]
    if not bottom_verts:
        # fallback, in case of floating precision issues
        bottom_verts = [v for v in bb_local if v.z == min_z]
    center_local = sum(bottom_verts, mathutils.Vector((0.0, 0.0, 0.0))) / len(bottom_verts)


    # Convert to world space
    center_world = obj.matrix_world @ center_local


    # Move 3D cursor to that point and set origin to cursor
    scene = bpy.context.scene
    prev_cursor = scene.cursor.location.copy()
    scene.cursor.location = center_world

    #Deselect all
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)

    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')

    #Move object to 0,0,0
    obj.location = (0.0, 0.0, 0.0)

    #Restore 3D cursor position
    scene.cursor.location = prev_cursor

    #Apply transforms
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return

def setup_ref_image(image_path,location=(0,0,0),scale=(1,1,1),rotation=(math.pi/2,0,0)):
    
    #Check image path
    if not os.path.isfile(image_path):
        print("Invalid image path")
        return None
    
    #get image resolution
    image = bpy.data.images.load(image_path)
    width = image.size[0]
    height = image.size[1]
    image.user_clear()

    #Get larger dimension
    size = width if width > height else height
    #Each meter of width is 256px
    pixelRatio = size / 1024

    bpy.ops.object.empty_image_add(filepath=image_path, 
                                   align='WORLD', 
                                   location=location, 
                                   scale=scale,
                                   rotation=rotation)
    
    image_plane = bpy.context.active_object
    image_plane.empty_display_size = pixelRatio
    image_plane.empty_image_offset[1] = 0

    image_plane.hide_select = True


    return image_plane

def check_arrow():
     if bpy.context.scene.ARROW.name not in bpy.data.objects:
         bpy.context.scene.ARROW = setup_ref_image(r"C:\Users\alber\Downloads\arrow.png",rotation=(0,0,0))
         
def setup_ref_images(url):
    # Clear existing reference images in "References" collection
    ref_col = make_collection("References")

    for obj in list(ref_col.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    # Add new reference images
    img1 = setup_ref_image(url, location=(-0.5, 0, 0), scale=(1, 1, 1), rotation=(math.pi/2, 0, -math.pi/2))
    if img1:
        ref_col.objects.link(img1)
        img1.hide_select = True

    img2 = setup_ref_image(r"C:\Users\alber\Downloads\arrow.png",rotation=(0,0,0))
    if img2:
        ref_col.objects.link(img2)
        img2.hide_select = True

    return
       

        
def make_collection(name):
    if name in bpy.data.collections:
        #set collection unclickable
        # bpy.data.collections["Collection"].hide_select = True

        # bpy.data.collections[name].hide_select = True
        return bpy.data.collections[name]
    else:
        new_col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(new_col)
        #set collection unclickable
        new_col.hide_select = True
        return new_col

def world_bbox_corners(obj):
    """Return world-space coordinates of the object's bounding box corners."""
    mat = obj.matrix_world
    return [mat @ mathutils.Vector(corner) for corner in obj.bound_box]

def bbox_min_max_z(obj):
    corners = world_bbox_corners(obj)
    zs = [c.z for c in corners]
    return min(zs), max(zs)

def compute_radius_from_dimensions(obj):
    # conservative estimate: half of the maximal object dimension
    dims = obj.dimensions
    return max(dims.x, dims.y, dims.z) * 1.2

def create_plane_at(obj, side_length):
    # Create a plane mesh and place it at object's XY center and at the object's minimum Z.
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    plane = bpy.context.active_object
    plane.name = obj.name + "_shadow_plane"

    # scale to requested side length (plane primitive has size 1 -> side length 2 by default, so scale by side_length/2)
    scale_factor = side_length / 2.0
    plane.scale = (scale_factor, scale_factor, 1.0)

    # center on object's XY and place at min Z
    bbox_min_z, _ = bbox_min_max_z(obj)
    # use object's world X/Y center
    world_center = obj.matrix_world.translation
    plane.location = (world_center.x, world_center.y, bbox_min_z)

    # apply transforms so UVs behave predictably
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    return plane

def create_image(name, width=128, height=128):
    # Create an RGBA image in Blender
    img = bpy.data.images.new(name, width=width, height=height, alpha=True, float_buffer=False)
    # Fill with white opaque initially
    pixels = [1.0, 1.0, 1.0, 1.0] * (width * height)
    img.pixels[:] = pixels
    return img

def ensure_material_with_image(obj, image):
    # Create a simple principled material that uses the given image as base color and assign it to obj
    mat = bpy.data.materials.new(name=image.name + "_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # clear nodes
    for n in nodes:
        nodes.remove(n)

    output = nodes.new(type='ShaderNodeOutputMaterial')
    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
    tex_node = nodes.new(type='ShaderNodeTexImage')
    tex_node.image = image
    tex_node.interpolation = 'Closest'

    # link: tex -> principled base color -> material output
    links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
    links.new(principled.outputs['BSDF'], output.inputs['Surface'])

    # assign material
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    return mat

def create_top_down_sun(location, name="Shadow_Sun" ):
    
    # Create a sun light positioned at location (x,y,z) and pointing down
    light_data = bpy.data.lights.new(name=name, type='SUN')
    light_data.energy = bpy.context.scene.strength
    light_data.angle = math.radians(bpy.context.scene.angle)  # softness of shadows
    light_obj = bpy.data.objects.new(name, light_data)
    bpy.context.collection.objects.link(light_obj)
    light_obj.location = location

 
    return light_obj

def create_x_suns_around(obj, distance):

    # Check for lights and delete them
    # for light in [o for o in bpy.context.scene.objects if o.type == 'LIGHT']:
    #     bpy.data.objects.remove(light, do_unlink=True)
    
    lights =[]
    obj_distance = obj.dimensions.x if obj.dimensions.x > obj.dimensions.y else obj.dimensions.y
    angle_increment = 360 / bpy.context.scene.numLights
    for i in range(bpy.context.scene.numLights):
        
        if bpy.context.scene.simetric:
            angle_random_offset = 0
        else:
            angle_random_offset = random.uniform(-angle_increment/4, angle_increment/4)
        angle_rad = math.radians(i * angle_increment+ angle_random_offset)
        x = obj.location.x + (obj_distance + distance) * math.cos(angle_rad)
        y = obj.location.y + (obj_distance + distance) * math.sin(angle_rad)
        _, max_z = bbox_min_max_z(obj)
        z = max_z + 5.0
        name = f"Shadow_Sun_{i+1}"
        light_data = bpy.data.lights.new(name=name,type='SUN')
        light_data.energy = bpy.context.scene.strength
        light_data.angle = math.radians(bpy.context.scene.angle)  # softness of shadows
        light_obj = bpy.data.objects.new(name, light_data)
        bpy.context.collection.objects.link(light_obj)
        light_obj.location = (x, y, z)
        # make the light point towards the object's center
        direction = (obj.location - light_obj.location).normalized()
        rot_quat = direction.to_track_quat('-Z', 'Y')
        light_obj.rotation_euler = rot_quat.to_euler()
        lights.append(light_obj)
    return lights

def set_object_as_shadow_caster_only(obj):
    # Make object invisible to camera but still cast shadows (Cycles: disable camera visibility)
    try:
        obj.cycles_visibility.camera = False
    except Exception:
        # some Blender versions may not have cycles_visibility; try alternate approach
        obj.hide_render = False
        # no perfect cross-version guarantee; keep original visible flag so it still casts shadow if possible
    return

def setup_render():
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.cycles.bake_type = 'SHADOW'
    bpy.context.scene.cycles.device = 'CPU'
    bpy.context.scene.cycles.samples = 64
    bpy.context.scene.render.bake.use_selected_to_active = False
    return

def make_shadow_plane(obj):
    
    setup_render()
    # Compute size/radius and plane placement
    radius = compute_radius_from_dimensions(obj)
    
    side_len = radius * 4.0
    if side_len <= 0.0:
        side_len = 1.0

    plane = create_plane_at(obj, side_len)
   

    # # create 128x128 image and material
    img = create_image(obj.name + "_shadow_tex", 128, 128)
    ensure_material_with_image(plane, img)

    #In case of multiple objects, isolate the object to render and then the plane
    isolate_mesh_render(obj)

    plane.hide_render = False

    bpy.ops.object.bake(type='SHADOW')

    rendered = img  # use the image we baked into
    # ensure image has pixels loaded
    rendered.pixels.foreach_get
    pixels = list(rendered.pixels[:])
    # pixels is a flat list [r,g,b,a, r,g,b,a, ...]
    w = rendered.size[0]
    h = rendered.size[1]
    for i in range(0, len(pixels), 4):
        r, g, b, a = pixels[i:i+4]
        # treat nearly-white as white
        pixels[i] = 0.0
        pixels[i+1] = 0.0
        pixels[i+2] = 0.0
        pixels[i+3] = 1-max(r, g, b)  # use min of RGB as alpha (black=0, white=1)

    # write pixels back into image
    rendered.pixels[:] = pixels

    return plane
    
def isolate_mesh_render(obj):
    if obj.type != 'MESH':
        return
    
    #Iterate and make render visibility false for all meshes
    for o in bpy.context.scene.objects:
        if o.type == 'MESH':
            o.hide_render = True
    
    obj.hide_render = False
    
def export_obj(obj):
    #Begin
    #Make suns
    lights = create_x_suns_around(obj, distance=1.0)

    #Make shadow plane
    plane = make_shadow_plane(obj)

    #Select only plane and target object
    bpy.ops.object.select_all(action='DESELECT')
    plane.select_set(True)
    obj.select_set(True)

    #Export selected objects
    bpy.ops.export_scene.gltf(filepath=os.path.join(bpy.context.scene.pathToFile,obj.name + ".glb"),export_format='GLB',check_existing=True,use_selection =True)

    #Delete plane and lights
    bpy.data.objects.remove(plane, do_unlink=True)
    for light in lights:
        bpy.data.objects.remove(light, do_unlink=True)
    

# ---------- Operator & Panel ----------
class OBJECT_OT_reset_pivot (bpy.types.Operator):
    bl_idname = "object.reset_pivot"
    bl_label = "Reset pivot to base center"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bpy.context.scene.targetObj is not None and bpy.context.scene.targetObj.type == 'MESH'

    def execute(self, context):
        
        fix_origin(bpy.context.scene.targetObj)
        return {'FINISHED'}

class OBJECT_OT_bake_shadow_texture(bpy.types.Operator):
    bl_idname = "object.bake_shadow_texture"
    bl_label = "Bake Shadow Texture"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scn = context.scene
        sel = context.active_object
        if not sel or sel.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh")
            return {'CANCELLED'}

        # store some original settings to restore later
        prev_engine = scn.render.engine
        # ensure Cycles for reliable shadow behavior
        scn.render.engine = 'CYCLES'
        bpy.context.scene.render.engine = 'CYCLES'
        bpy.context.scene.cycles.device = 'GPU'
        bpy.context.scene.cycles.bake_type = 'SHADOW'
        bpy.context.scene.cycles.denoiser = 'OPTIX'
        bpy.context.scene.cycles.samples = 64

        # Compute size/radius and plane placement
        radius = compute_radius_from_dimensions(sel)
        # Use side length = radius * 2 so plane covers the object's footprint
        side_len = radius * 4.0
        if side_len <= 0.0:
            side_len = 1.0

        plane = create_plane_at(sel, side_len)
        # Move plane to "Shadow" collection
        # shadow_col = make_collection("Shadow")
        # if plane.name not in shadow_col.objects:
        #     shadow_col.objects.link(plane)

        #Move object to "Asset" collection
        # asset_col = make_collection("Asset")
        # if sel.name not in asset_col.objects:
        #     asset_col.objects.link(sel)

        # create 128x128 image and material
        img = create_image(sel.name + "_shadow_tex", 128, 128)
        ensure_material_with_image(plane, img)



        # Create light above object
        # _, max_z = bbox_min_max_z(sel)
        # light_height = max_z + (radius * 3.0 if radius > 0 else 3.0)
        # light = create_top_down_sun((sel.location.x, sel.location.y, light_height))
        create_x_suns_around(sel, distance=1.0)

   

   
        # output file
        blend_dir = bpy.path.abspath("//")


        bpy.ops.object.bake(type='SHADOW')


        
        rendered = img  # use the image we baked into
        # ensure image has pixels loaded
        rendered.pixels.foreach_get
        pixels = list(rendered.pixels[:])
        # pixels is a flat list [r,g,b,a, r,g,b,a, ...]
        w = rendered.size[0]
        h = rendered.size[1]
        for i in range(0, len(pixels), 4):
            r, g, b, a = pixels[i:i+4]
            # treat nearly-white as white

            pixels[i] = 0.0
            pixels[i+1] = 0.0
            pixels[i+2] = 0.0
            pixels[i+3] = 1-max(r, g, b)  # use min of RGB as alpha (black=0, white=1)


        # write pixels back into image
        rendered.pixels[:] = pixels
        # save final PNG
        final_path = os.path.join(blend_dir, "baked_shadow.png")
        rendered.filepath_raw = final_path
        rendered.file_format = 'PNG'
        rendered.save()



        self.report({'INFO'}, f"Baked shadow saved to: {final_path}")
        # context.scene.pathToFile

        return {'FINISHED'}
         
class OBJECT_OT_export_obj_glb(bpy.types.Operator):
    """Export the selected object as a GLB file to the specified folder."""
    
    bl_idname = "object.export_glb"
    bl_label = "Export GLB"
    bl_options = {'REGISTER', 'UNDO'}

    # @classmethod
    # def poll(cls, context):
    #     # Return True when we want the operator to be runnable.
    #     # Typical checks: context.active_object, context.mode, object type, etc.
    #     runnable = True
    #     if bpy.context.scene.targetObj == None or bpy.context.scene.targetObj.type != 'MESH':
    #         runnable = False
    #         return runnable
    #     if not os.path.isdir(bpy.context.scene.pathToFile):
    #         runnable = False
    #         return runnable
    #     if (bpy.context.scene.targetObj.dimensions.z < MEASUREMENTS[bpy.context.scene.measurements][0] or bpy.context.scene.targetObj.dimensions.z > MEASUREMENTS[bpy.context.scene.measurements][1]):
    #         runnable = False
    #         return runnable
    
    #     return runnable

    def execute(self, context):
        
        mode = bpy.context.mode

        #set to object mode
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        #Initial checks
        if bpy.context.scene.targetObj == None:
            self.report({'ERROR'}, "No target object selected")
            return {'CANCELLED'}
        if not os.path.isdir(bpy.context.scene.pathToFile):
            self.report({'ERROR'}, "Invalid path")
            return {'CANCELLED'}
        # if (bpy.context.scene.targetObj.dimensions.z < MEASUREMENTS[bpy.context.scene.measurements][0] or bpy.context.scene.targetObj.dimensions.z > MEASUREMENTS[bpy.context.scene.measurements][1]):
        #     self.report({'ERROR'}, "Wrong object height for selected measurement")
        #     return {'CANCELLED'}
        
        if bpy.context.scene.targetObj.type != 'MESH':
            self.report({'ERROR'}, "Target object is not a mesh")
            return {'CANCELLED'}
        
        #Begin
        #Make suns
        lights = create_x_suns_around(bpy.context.scene.targetObj, distance=1.0)

        #Make shadow plane
        plane = make_shadow_plane(bpy.context.scene.targetObj)

        #Select only plane and target object
        bpy.ops.object.select_all(action='DESELECT')
        plane.select_set(True)
        bpy.context.scene.targetObj.select_set(True)

        #Export selected objects
        bpy.ops.export_scene.gltf(filepath=os.path.join(bpy.context.scene.pathToFile,bpy.context.scene.targetObj.name + ".glb"),export_format='GLB',check_existing=True,use_selection =True)

        #Delete plane and lights
        bpy.data.objects.remove(plane, do_unlink=True)
        for light in lights:
            bpy.data.objects.remove(light, do_unlink=True)

        #Set active mode
        bpy.context.view_layer.objects.active = bpy.context.scene.targetObj

        return {'FINISHED'}

class OBJECT_OT_export_collection_glb(bpy.types.Operator):
    """Export all mesh objects in the selected collection as individual GLB files to the specified folder."""
    
    bl_idname = "object.export_coll_glb"
    bl_label = "Export GLB"
    bl_options = {'REGISTER', 'UNDO'}

    # @classmethod
    # def poll(cls, context):
    #     # Return True when we want the operator to be runnable.
    #     # Typical checks: context.active_object, context.mode, object type, etc.
    #     runnable = True
    #     if bpy.context.scene.Collection == None:
    #         runnable = False
    #         return runnable
    #     if not os.path.isdir(bpy.context.scene.pathToFile):
    #         runnable = False
    #         return runnable
    #     if len(bpy.context.scene.Collection.all_objects) == 0:
    #         runnable = False
    #         return runnable
        

    #     return runnable

    def execute(self, context):
        #Initial checks
        if bpy.context.scene.Collection == None:
            self.report({'ERROR'}, "No collection selected")
            return {'CANCELLED'}
        if not os.path.isdir(bpy.context.scene.pathToFile):
            self.report({'ERROR'}, "Invalid path")
            return {'CANCELLED'}
        if len(bpy.context.scene.Collection.all_objects) == 0:
            self.report({'ERROR'}, "Collection is empty")
            return {'CANCELLED'}
        
        #Begin
        meshes = [obj for obj in bpy.context.scene.Collection.all_objects if obj.type == 'MESH']

        mode = bpy.context.mode

        #set to object mode
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        

        for mesh in meshes:
            if mesh.type != 'MESH':
                self.report({'ERROR'}, mesh.name + " is not a mesh" )
                print( mesh.name + " is not a mesh")
                continue
            # if (mesh.dimensions.z < MEASUREMENTS[bpy.context.scene.measurements][0] or mesh.dimensions.z > MEASUREMENTS[bpy.context.scene.measurements][1]):
            #     self.report({'ERROR'}, "Wrong object height for selected measurement: " + mesh.name)
            #     print("Wrong object height for selected measurement: " + mesh.name)
            #     #Skip this object
            #     continue
            export_obj(mesh)
            
        
        return {'FINISHED'}

class OBJECT_OT_setup_ref_images(bpy.types.Operator):
    bl_idname = "object.setup_ref_images"
    bl_label = "Setup reference images"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        
        setup_ref_images(r"C:\Users\alber\Downloads\Image20251003133529.png")
    
        

        return {'FINISHED'}


class VIEW3D_PT_shadow_baker_panel(bpy.types.Panel):
    bl_label = "Shadow Baker"
    bl_idname = "VIEW3D_PT_shadow_baker"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Shadow Baker'

    def draw(self, context):
        layout = self.layout

        make_collection("References")
        # check_arrow()

        resetPositionBox = layout.box()
        resetPositionBox.label(text="1º Setup", icon='WORLD_DATA')
        col = resetPositionBox.column(align=True)
        row = col.row(align=True)
        row.prop(context.scene, 'targetObj')
        row = col.row(align=True)
        resetPositionBox.operator(OBJECT_OT_reset_pivot.bl_idname, text="Reset pivot to base center")


        measurementBox = layout.box()
        measurementBox.label(text="2º Measurements", icon='MESH_CUBE')
        col3 = measurementBox.column(align=True)
        row = col3.row(align=True)
        measurementBox.operator(OBJECT_OT_setup_ref_images.bl_idname, text="Setup reference images")
        row = col3.row(align=True)
        row.prop(context.scene, 'measurements')
        row = col3.row(align=True)
        row.label(text="Target height range: " + str(MEASUREMENTS[context.scene.measurements][0]) + "m - " + str(MEASUREMENTS[context.scene.measurements][1]) + "m")
        row = col3.row(align=True)
        if bpy.context.active_object != None and bpy.context.active_object.type == 'MESH':
            row.label(text=f"Object height: {bpy.context.active_object.dimensions.z:.2f}m")
            row = col3.row(align=True)
            if bpy.context.active_object.dimensions.z > MEASUREMENTS[context.scene.measurements][0] and bpy.context.active_object.dimensions.z < MEASUREMENTS[context.scene.measurements][1]:
                row.label(text="Within range", icon='CHECKMARK')
            else:
                row.label(text="Outside range", icon='ERROR')
        else:
            row.label(text="No active object")

        boxShadows = layout.box()
        boxShadows.label(text="3º Shadow Baking", icon='LIGHT_SUN')
        col1 = boxShadows.column(align=True)
        row = col1.row(align=True)
        row.prop(context.scene, 'numLights')
        row = col1.row(align=True)
        row.prop(context.scene, 'simetric')
        row = col1.row(align=True)
        row.prop(context.scene, 'angle')
        row = col1.row(align=True)
        row.prop(context.scene, 'strength')
       
        exportBox = layout.box()
        exportBox.label(text="4º GLB Exporter", icon='EXPORT')
        col2 = exportBox.column(align=True)
        row = col2.row(align=True)
        row.prop(context.scene, 'pathToFile')
        row = col2.row(align=True)
        row.prop(context.scene, 'ExportCollection')
        if context.scene.ExportCollection:
            row = col2.row(align=True)
            row.prop(context.scene, 'Collection')
            
        if context.scene.ExportCollection:
            row = col2.row(align=True)
            exportBox.operator(OBJECT_OT_export_collection_glb.bl_idname, text="Export")
        if not context.scene.ExportCollection:  
            row = col2.row(align=True)
            exportBox.operator(OBJECT_OT_export_obj_glb.bl_idname, text="Export")



     
klases = [
        OBJECT_OT_bake_shadow_texture, VIEW3D_PT_shadow_baker_panel,
        OBJECT_OT_setup_ref_images,
        OBJECT_OT_reset_pivot,
        OBJECT_OT_export_obj_glb,
        OBJECT_OT_export_collection_glb,
          ]

# ---------- Registration ----------

def register():
    
    for cls in klases:
        bpy.utils.register_class(cls)

    for (prop_name, prop_value) in PROPS:
        setattr(bpy.types.Scene, prop_name, prop_value)

def unregister():

    for cls in klases:
        bpy.utils.unregister_class(cls)

    for (prop_name, _) in PROPS:
        delattr(bpy.types.Scene, prop_name)

if __name__ == '__main__':
    register()

  
