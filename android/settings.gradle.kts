// Hoopster Android app (task 2026-11-09_android_app, REQ-085). Built only on GitHub
// (.github/workflows/android-build.yml); nothing is installed on the Overseer's PC.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "HoopsterApp"
include(":app")
