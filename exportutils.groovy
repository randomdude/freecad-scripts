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

String locateURLForPreviousArtifact(thisArtifact)
{
	// Since our artifacts are suffixed with the build number, look at the last successful build and find the
	// URL pointing to the artifact corresponding to the provided (if any).
	
	// Get a list of artifacts..
	$curlStatus = bat returnStatus: true, script: "curl --fail http://jenkins.home.gamesfairy.co.uk/job/$JOB_NAME/lastStableBuild/api/python -o lastStableBuild.json"
	if ($curlStatus != 0)
		return null
	// Read response from Jenkins API and deserialise. Note that we need to escape some text since jenkins will provide the literal "None" without proper
	// quoting.
	jsonData = readFile('lastStableBuild.json')
	jsonData = jsonData.replace("None", "\"None\"")
	def lastBuildInfo = readJSON(text: jsonData)

	// Prune the build number from the thing we're finding
	toFind = thisArtifact.replaceAll(/_[0-9]*/, "")
	
	// Now iterate over artifacts, returning a URL for any that match.
	toRet = null
	lastBuildInfo["artifacts"].each { artifactInfo ->
		artifactName = artifactInfo["fileName"]
		artifactNameWithoutBuildNumber = artifactName.replaceAll(/_[0-9]*/, "")
		if (artifactNameWithoutBuildNumber == toFind) {
			toRet = "http://jenkins.home.gamesfairy.co.uk/job/${JOB_NAME}/lastStableBuild/artifact/${artifactName}"
		}
	}
	return toRet
}

def archiveGCodeAndScreenshotFiles(projName, outputPrefix)
{
    // Source files, which already exist
    outputGCodeFilename = "${outputPrefix}.gcode"
    outputScreenshotFilename = "${outputPrefix}.png"

    // Friendly names of outputs which we may archive (renamed from our source).
    archivedOutputGCodeFilename = "${projName}_${BUILD_NUMBER}.gcode"
    archivedoutputScreenshotFilename = "${projName}_${BUILD_NUMBER}.png"
    diffFilename = "${projName}_${BUILD_NUMBER}_diff.png"

    // We should have some nice gcode now, and a screenshot. Rename them to include the build number before archiving them.
    bat "copy ${outputGCodeFilename} ${archivedOutputGCodeFilename}"
    bat "copy ${outputScreenshotFilename} ${archivedoutputScreenshotFilename}"
    archiveArtifacts artifacts: archivedOutputGCodeFilename, onlyIfSuccessful: true
    archiveArtifacts artifacts: archivedoutputScreenshotFilename, onlyIfSuccessful: true

    // Make a 'diff' of the exported image against the previous successful build.
    // The new 'diff' image will have the original image in green, new things in blue, and old things (no longer present) in red.
    previousScreenshotURL = locateURLForPreviousArtifact(archivedoutputScreenshotFilename)
    if (previousScreenshotURL != null)
    {
        bat script: "curl --fail ${previousScreenshotURL} -o old.png"
        // Remove the rapid moves (in red)
        bat script: 'magick old.png -fill white -fuzz 60%% -opaque "rgb(255,0,0)" -trim old2.png'
        bat script: "magick ${outputScreenshotFilename} -fill white -fuzz 60%% -opaque \"rgb(255,0,0)\" -trim exported2.png"
        // And create the diff.
        bat script: "magick old2.png exported2.png -compose difference  -metric AE -fuzz 15%% -compare -background white -alpha remove -alpha off -auto-level ${diffFilename}"

        // We count the number of blue and red pixels. If there are none, then there are no changes in this file at all.
        $newPixels = bat(returnStdout: true, script: "@magick convert ${diffFilename} -fill black -fuzz 50%% +opaque \"rgb(0,0,255)\" -format \"%%[fx:w*h*mean]\" info:")
        $oldPixels = bat(returnStdout: true, script: "@magick convert ${diffFilename} -fill black -fuzz 50%% +opaque \"rgb(219,0,0)\" -format \"%%[fx:w*h*mean]\" info:")

        echo "Newly-added   pixel count: ${$newPixels.trim()}"
        echo "Newly-removed pixel count: ${$oldPixels.trim()}"

        if ($newPixels.trim() != "0" || $oldPixels.trim() != "0")
        {
            archiveArtifacts artifacts: diffFilename, onlyIfSuccessful: true
        }
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
		}
		else
		{
			projDir = projPathEl[0..-2].join('\\')
			projName = projPathEl[-1]
		}

		scriptName = "export-${projName}.py"
		bat "copy freecad-scripts\\exportutils.py ${projDir}\\"

		dir(projDir)
		{
			sourceFilename = "${projName}.FCStd"
			tempName = "exported_${projName}.FCStd"
			scriptName = "export-${projName}.py"

			// Now we can start FreeCAD and run our scripts on a copy of our design.
			bat "copy ${sourceFilename} ${tempName}"
			$s = bat returnStatus: true, script: "\"C:\\Program Files\\FreeCAD 0.19\\bin\\FreeCAD.exe\" --log-file ${WORKSPACE}\\freecad.log ${tempName} ${scriptName}"

			outputPrefix = "exported_${projName}".replace('-', '_')
			archiveGCodeAndScreenshotFiles(projName, outputPrefix)

			# Also see if engravings are present.
			$s = bat returnStatus: true, script: "dir \"${outputPrefix}_engravings.gcode\""
			if ($s == 0)
			{
				archiveGCodeAndScreenshotFiles(projName + "_engravings", outputPrefix + "_engravings")
			}
		}
	}
}

return this