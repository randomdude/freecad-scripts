import FreeCAD
import FreeCADGui
import Part

import CompoundTools.Explode

from panel import multiplejoins
from lasercut.material import MaterialProperties
from lasercut.tabproperties import TabProperties

import PathScripts.PathProfile
from PathScripts import PathJobGui, PathInspect
from PathScripts.PathPost import CommandPathPost
import PathScripts.PathJob 

import math

#
# This class holds factory-style definitions for each of our materials.
# Each thickness of material defines a speed, defined as a multiplier
# to the base speed of 300 mm/m, and a 'kerf', expressing the diameter
# of material destroyed when the laser is on without moving.
#
# The values are taken from the suggested values for my laser cutter, ie
# https://lionsforge.com.sg/wp-content/uploads/2019/09/CraftLaser-Settings-Guide.pdf
class cutterMaterial:
	def __init__(self, thickness, feedSpeed, rapidSpeed, kerf = 0.15):
		self.thickness = thickness
		self.rapidSpeed =  rapidSpeed
		self.feedSpeed  = feedSpeed
		self.kerf = kerf
	
	def bamboo(thickness):
		return cutterMaterial(thickness, (300 / 60) * 1.7, 3000 / 60, 0.15)

	def mdf(thickness):
		return cutterMaterial(thickness, (300 / 60) * 1.7, 3000 / 60)

	def acrylic(thickness):
		speeds = { 
			1: 2.5, 
			2: 2.0, 
			3: 1.3,
			5: 1.0
		}
		if thickness not in speeds.keys:
			raise Exception("Speed multiplier for acrylic at thickness " + thickness + " mm not defined")
		return cutterMaterial(thickness, (300 / 60) * speeds[thickness], 3000 / 60)

class tabbedObjectBuilder:
	def __init__(self, objectNames, material):
		self.objectNames = objectNames
		self.material = material

		# Now create the interlockingJoin object we'll use to create our tabs.
		doc = FreeCAD.ActiveDocument
		self.groupJoin = doc.addObject("Part::FeaturePython", "interlockingJoin")
		self.joinGroup = multiplejoins.MultipleJoinGroup(self.groupJoin)

		# We'll add all the objects we'll be adding tabs to..
		for obj in map(lambda x: doc.getObjectsByLabel(x)[0], objectNames):
			material = MaterialProperties(type=MaterialProperties.TYPE_LASER_CUT, name=obj.Name, label=obj.Label, freecad_object=obj, thickness = self.material.thickness)
			# We compensate for this later on, in the gcode generation step, not here.
			material.laser_beam_diameter = 0 
			self.groupJoin.parts.append(material)
	
	# Given the name of an object and a normal, add tabs to all faces which are pointing in the same direction as that normal.
	# Optionally, specify 'requiredX' in order to only add faces with the specified X value.
	def createTabsByFaceNormal(self, objectName, normalToFind, requiredX = None, requiredY = None, requiredZ = None, tabWidth = 1, tabNumber = 2, tabShift = 0.0, tabRatio = 1.0, testFunc = None):
		obj = FreeCAD.ActiveDocument.getObjectsByLabel(objectName)[0]
		faceidx = 1
		for face in obj.Shape.Faces:
			if len(face.Vertexes) > 2:
				# Does this face point in the right direction?
				if abs(face.normalAt(0,0) - normalToFind).Length < 0.01:
					# It does! Is there an X-filter requested? If so, apply that.
					if (requiredX is None or abs(face.Vertexes[0].X - requiredX) < 0.01) and (requiredZ is None or abs(face.Vertexes[0].Z - requiredZ) < 0.01) and (requiredY is None or abs(face.Vertexes[0].Y - requiredY) < 0.01):
						# And check for a condition-testing function.
						if testFunc is None or testFunc(face):
							tabProps = TabProperties(freecad_face=face, freecad_obj_name=obj.Name, face_name="Face%d" % faceidx, tabs_number = tabNumber, tabs_width = tabWidth, tabs_shift = tabShift, interval_ratio = tabRatio, tab_type=TabProperties.TYPE_TAB)
							self.groupJoin.faces.append(tabProps)
			faceidx = faceidx + 1

	def getFaces(self):
		return self.groupJoin.faces

	def execute(self):
		self.groupJoin.need_recompute = True
		self.joinGroup.execute(self.groupJoin)
		
		# Find the things we've tabbed.
		# If we tab'bed any arrays, ensure we now work with their children and not the array itself.
		tabbedObjects = []
		for objName in self.objectNames:
			tabObjName = objName.replace(".", "_").replace("-", "_") + "_tab"
			objs = FreeCAD.ActiveDocument.getObjectsByLabel(tabObjName)
			if len(objs) == 0:
				raise Exception("Can't find tabbed result of object %s" % (objName))
			obj = objs[0]
			
			if obj.Shape.__class__ is Part.Compound:
				_, components = CompoundTools.Explode.explodeCompound(obj)
				components[0].Base.Visibility = False
				for component in components:
					tabbedObjects.append(component)
			else:
				tabbedObjects.append(obj)

		for obj in FreeCAD.activeDocument().Objects:
			if obj in tabbedObjects:
				obj.Visibility = True
			else:
				obj.Visibility = False

		return tabbedObjects

class exportutils:
	def __init__(self, objectsToCut, material):
		self.material = material
		self.objectsToCut = objectsToCut
		self.gcode = None

	def rotateAndPositionAllObjectsOnZ(self):
		for obj in self.objectsToCut:
			self.rotateAndPositionObjectOnZ(obj)

	def rotateAndPositionObjectOnZ(self, obj):
		obj.Placement.Rotation.Angle = 0
		# Recompute if neccessary, to generate bounding boxes
		if obj.MustExecute:
			obj.recompute()
		
		# Find which dimension is the same as material thickness, and rotate so that is facing up (ie, +Z).
		if abs(obj.Shape.BoundBox.XLength - self.material.thickness) < 0.01:
			obj.Placement.Rotation.Axis = FreeCAD.Vector(0,1,0)
		elif abs(obj.Shape.BoundBox.YLength - self.material.thickness) < 0.01:
			obj.Placement.Rotation.Axis = FreeCAD.Vector(1,0,0)
		elif abs(obj.Shape.BoundBox.ZLength - self.material.thickness) < 0.01:
			obj.Placement.Rotation.Axis = FreeCAD.Vector(0,0,1)
		else:
			raise Exception("Don't know how to rotate object " + obj.Name + " to put it on the XY face")
		obj.Placement.Rotation.Angle = math.pi/2
		
		obj.recompute()
		
		# Now we should move this object along the Z-axis so that it aligns nicely on z=0.
		# Find a face which is parallel with the Z-axis
		foundFace = None
		faceidx = 1
		for face in obj.Shape.Faces:
			if abs(face.normalAt(0,0) - FreeCAD.Vector(0,0,1)).Length < 0.01:
				foundFace = face
				break
			faceidx = faceidx + 1

		# Now align this face with Z=0.
		obj.Placement.Base.z = -face.Vertexes[0].Z + obj.Shape.BoundBox.ZLength
		obj.recompute()

	def placeInRow(self, objectsToPlace, startPosX = 0, startPosY = 0, spaceBetweenObjects = 1):
		pos = startPosX
		for obj in objectsToPlace:
#			# If this object is wider (in X) than it is in Y, rotate it around Z so it doesn't waste space.
#			if obj.Shape.BoundBox.XLength > obj.Shape.BoundBox.YLength:
#				# Rotate around Z.
#				obj.Placement.Rotation = obj.Placement.Rotation.multiply(FreeCAD.Rotation(FreeCAD.Base.Vector(0,0,1),90))
			obj.Placement.Base.x = obj.Placement.Base.x - obj.Shape.BoundBox.XMin + pos
			obj.Placement.Base.y = obj.Placement.Base.y - obj.Shape.BoundBox.YMin + startPosY
			pos = obj.Shape.BoundBox.XMax + spaceBetweenObjects

	def execute(self):
		for x in self.objectsToCut:
			x.recompute()
		# Ensure none of our objects are outside the printable area
		minX = min(map(lambda x: x.Shape.BoundBox.XMin, self.objectsToCut))
		minY = min(map(lambda x: x.Shape.BoundBox.YMin, self.objectsToCut))
		if minX < 0 or minY < 0:
			raise Exception("Objects are not all in positive X and Y space")

		## make job object and set some basic properties
		cncjob = PathScripts.PathJob.Create('Myjob', self.objectsToCut)
		cncjob.PostProcessor = 'lcnclaser'
		cncjob.PostProcessorArgs = "--no-show-editor"

		# We can set up our tool now, and a toolcontroller to control it.
		lasertool = PathScripts.PathToolBit.Factory.Create('laserbeam')
		toolController = PathScripts.PathToolController.Create('lasercontroller')
		toolController.Tool = lasertool
		lasertool.Diameter = self.material.kerf
		lasertool.Label = "laserBeam"

		cncjob.SetupSheet.HorizRapid = self.material.rapidSpeed
		cncjob.SetupSheet.VertRapid = self.material.rapidSpeed
		toolController.HorizFeed = self.material.feedSpeed
		toolController.VertFeed  = self.material.feedSpeed
		cncjob.Tools.Group = [ toolController ]

		# Select the relavant face on each of our objects and profile its child edges.
		# Store an array of tuples, each containing the object and the face name.
		toCut = []
		for obj in cncjob.Model.Group:
			faceIdx = 1
			for face in obj.Shape.Faces:
				# Does this face point upward?
				if abs((face.normalAt(0,0) - FreeCAD.Vector(0, 0, 1)).Length < 0.1):
					# And is it the top one?
					if abs(face.Vertexes[0].Z - self.material.thickness) < 0.01:
						# It does, so profile this.
						toCut.append((obj, 'Face%d' % faceIdx))
					else:
						# Check if it is at the bottom. If not, alert the user - it may be a situation the 2D laser cutter cannot cut.
						if abs(face.Vertexes[0].Z) > 0.01:
							print("Object %s face Face%d is at Z depth %d; not at Z=0 or Z=material.thickness, please check it is as intended" % (obj.Label, face.Vertexes[0].Z, faceIdx))
				faceIdx = faceIdx + 1

		# Now we can make a path for each face we'll be profiling.
		cutObjs = PathScripts.PathProfile.Create("cutOutsideObjects")
		cutObjs.processHoles = True
		cutObjs.processCircles = True
		cutObjs.Base = toCut
		# We set start and final depth the same so that we get a 2D laser-style output.
		cutObjs.ToolController = toolController
		cutObjs.setExpression('FinalDepth', None)
		cutObjs.setExpression('StartDepth', None)
		cutObjs.FinalDepth = self.material.thickness
		cutObjs.StartDepth = self.material.thickness

		cncjob.recompute(True)

		if cncjob.Stock.Shape.BoundBox.XLength > 420 or cncjob.Stock.Shape.BoundBox.YLength > 297:
			raise Exception("Cut is too large for laser cutter bed")

		# TODO: check against size of the wooden sheets we cut

		# Post-process the job now
		p = CommandPathPost()
		s, self.gcode, filename = p.exportObjectsWith([cutObjs], cncjob, False)
	
	def saveGCode(self, filename = None):
		if self.gcode is None:
			raise Exception(".execute not called before attempt to save gcode")
		if filename is None:
			filename = FreeCAD.ActiveDocument.Name + ".gcode"
		with open(filename, 'w') as f:
			f.write(self.gcode)

	def saveScreenshotOfPath(self, filename = None):
		if self.gcode is None:
			raise Exception(".execute not called before attempt to save screenshot")

		if filename is None:
			filename = FreeCAD.ActiveDocument.Name + ".png"

		# Show only the gcode operations themselves, hiding everything else
		for obj in FreeCAD.activeDocument().Objects:
			obj.Visibility = False

		camOps = FreeCAD.activeDocument().getObjectsByLabel("Operations")[0]
		camOps.Visibility = True

		# We take a snapshot using the raster saveImage instead of as a vector image.
		# Turn off the gradient background
		pg = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/View")
		pg.SetUnsigned("BackgroundColor2", 0xffffffff)
		pg.SetUnsigned("BackgroundColor3", 0xffffffff)

		try:
			v = FreeCADGui.activeDocument().activeView()
			v.viewIsometric()
			v.setViewDirection((0,0,-1))
			v.fitAll(1)
			v.saveImage(filename)
		finally:
			# Restore original background cols. FIXME: hope the user didn't set their own since they'll be reset..
			pg.RemUnsigned("BackgroundColor2")
			pg.RemUnsigned("BackgroundColor3")