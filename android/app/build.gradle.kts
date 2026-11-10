// The app module: one activity showing the Hoopster site in a WebView. No libraries beyond
// the Android platform itself (no AndroidX), as approved on 2026-11-09.
plugins {
    id("com.android.application")
}

// Set by the GitHub workflow: the run number makes every build an update of the last one.
val versionNumber: Int = (System.getenv("HOOPSTER_VERSION_CODE") ?: "1").toInt()
// The signing key, decoded by the workflow from the repository secrets (android/README.md).
val keystoreFile: String? = System.getenv("HOOPSTER_KEYSTORE_FILE")

android {
    namespace = "app.hoopster.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.hoopster.android"
        minSdk = 26
        targetSdk = 36
        versionCode = versionNumber
        versionName = "1.0.$versionNumber"
    }

    signingConfigs {
        create("release") {
            if (keystoreFile != null) {
                storeFile = file(keystoreFile)
                storePassword = System.getenv("HOOPSTER_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("HOOPSTER_KEY_ALIAS")
                keyPassword = System.getenv("HOOPSTER_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
