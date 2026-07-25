import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    // AGP 9+ has built-in Kotlin support; applying org.jetbrains.kotlin.android
    // on top of it is an error. See https://kotl.in/gradle/agp-built-in-kotlin
    id("com.android.application")
}

android {
    namespace = "com.yoink"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.yoink"
        // 26 (Android 8): MediaPipe tasks-vision needs 24, and 26 lets us use
        // notification channels without a legacy branch. Nothing older matters.
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    androidResources {
        // The MediaPipe model is memory-mapped from assets; compressing it
        // would force a copy to disk at load time.
        noCompress += "task"
    }
}

kotlin {
    compilerOptions {
        jvmTarget = JvmTarget.JVM_17
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    // LifecycleService: CameraX binds use cases to a LifecycleOwner, and in K2
    // that owner is the service, not the Activity. That single swap is what
    // keeps the camera streaming while the app is backgrounded.
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")

    // Camera
    implementation("androidx.camera:camera-core:1.4.1")
    implementation("androidx.camera:camera-camera2:1.4.1")
    implementation("androidx.camera:camera-lifecycle:1.4.1")
    implementation("androidx.camera:camera-view:1.4.1")

    // Hand landmarks
    implementation("com.google.mediapipe:tasks-vision:0.10.18")

    // Mesh networking (K3). NSD for discovery comes from the platform.
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    testImplementation("junit:junit:4.13.2")
    // android.jar's org.json is a stub that throws "Stub!" on every call, so a
    // JVM test against it proves nothing. This shadows it with the real
    // implementation Android ships.
    testImplementation("org.json:json:20240303")
}
