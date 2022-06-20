def installReqs()
{
	stage("Installing FreeCAD and requirements")
	{
		// We'll need imagemagick to diff our produced image
		bat "choco install -y imagemagick"
		
		// Install FreeCAD itself
		bat "choco install -y FreeCAD --version 0.19.4"
		
		// And our fork of FreeCAD's LCInterlocking plugin
		dir ("${env.APPDATA}\\FreeCAD\\Mod\\LCInterlocking")
		{
			checkout([$class: 'GitSCM', branches: [[name: '*/master']], userRemoteConfigs: [[url: 'http://github.com/randomdude/LCInterlocking.git']]])
		}
		
		// We'll need our laser post-processor, too.
		checkout([$class: 'GitSCM', branches: [[name: '*/master']], extensions: [[$class: 'RelativeTargetDirectory', relativeTargetDir: 'post']], userRemoteConfigs: [[url: 'http://gitea/aliz/freecad-lcnclaser.git']]])
		bat 'copy post\\lcnclaser_post.py "C:\\program files\\freecad 0.19\\Mod\\Path\\PathScripts\\post\\"'

		// TODO: this doesn't seem to work, we always need to close and restart the jenkins runner :(
		bat "refreshenv"
	}
}

def doBuildForFCStdFile(projPath)
{
	stage("Generating gcode")
	{
		// If this is in a subdir, descend into it, and a copy the exportutils.py script into it too.
		projPathEl = "${projPath}".split('\\\\')
		if (projPathEl.length == 1)
		{
			projDir = '.'
			projName = projPath
			scriptName = "export-${projName}.py"
		}
		else
		{
			projDir = projPathEl[0]
			projName = projPathEl[1]
			scriptName = "export-${projName}.py"
			bat "copy exportutils.py ${projDir}\\"
		}

		dir(projDir)
		{
			sourceFilename = "${projName}.FCStd"
			tempName = "exported_${projName}.FCStd"
			scriptName = "export-${projName}.py"
			outputGCodeFilename = "exported_${projName}.gcode".replace('-', '_')
			outputScreenshotFilename = "exported_${projName}.png".replace('-', '_')
			diffFilename = "exported_${projName}_diff.png"
			
			// Now we can start FreeCAD and run our scripts on a copy of our design.
			bat "copy ${sourceFilename} ${tempName}"
			$s = bat returnStatus: true, script: "\"C:\\Program Files\\FreeCAD 0.19\\bin\\FreeCAD.exe\" --log-file ${WORKSPACE}\\freecad.log ${tempName} ${scriptName}"
			
			// We should have some nice gcode now, and a screenshot.
			archiveArtifacts artifacts: outputGCodeFilename, onlyIfSuccessful: true
			archiveArtifacts artifacts: outputScreenshotFilename, onlyIfSuccessful: true

			// Make a 'diff' of the exported image against the previous successful build.
			// The new 'diff' image will have the original image in green, new things in blue, and old things (no longer present) in red.
			$curlStatus = bat returnStatus: true, script: "curl --fail http://jenkins.home.gamesfairy.co.uk/job/rackmount-ossc/lastStableBuild/artifact/${outputScreenshotFilename} -o old.png"
			if ($curlStatus == 0)
			{
				// Remove the rapid moves (in red)
				bat script: 'magick old.png -fill white -fuzz 60%% -opaque "rgb(255,0,0)" old2.png'
				bat script: "magick ${outputScreenshotFilename} -fill white -fuzz 60%% -opaque \"rgb(255,0,0)\" exported2.png"
				// And create the diff.
				bat script: "magick old2.png exported2.png -compose difference  -metric AE -fuzz 15%% -compare -background white -alpha remove -alpha off -normalize -trim ${diffFilename}"
				
				// We count the number of blue and red pixels. If there are none, then there are no changes in this file at all.
				$newPixels = bat(returnStdout: true, script: "@magick convert ${diffFilename} -fill black -fuzz 10%% +opaque \"rgb(0,0,255)\" -fill white -fuzz 10%% -opaque \"rgb(0,0,255)\" -format \"%%[fx:w*h*mean]\" info:")
				$oldPixels = bat(returnStdout: true, script: "@magick convert ${diffFilename} -fill black -fuzz 10%% +opaque \"rgb(219,0,0)\" -fill white -fuzz 10%% -opaque \"rgb(219,0,0)\" -format \"%%[fx:w*h*mean]\" info:")
				
				echo "Newly-added   pixel count: ${$newPixels.trim()}"
				echo "Newly-removed pixel count: ${$oldPixels.trim()}"
				
				if ($newPixels.trim() != "0" || $oldPixels.trim() != "0")
				{
					archiveArtifacts artifacts: diffFilename, onlyIfSuccessful: true
				}
			}
		}
	}
}

return this