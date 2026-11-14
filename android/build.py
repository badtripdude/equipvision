"""Build the Android application without Gradle: resources, Java, dex, package, align, sign.

    py android/build.py [sdk_dir]              default: tmp/android-sdk (see fetch_sdk.py)

Needs a JDK 17+ (JAVA_HOME, or the runtime bundled with a JetBrains IDE) and the SDK packages
fetched by fetch_sdk.py. The result is android/build/EquipVision.apk.

The application is signed with a key generated on the first build and kept in android/keystore
(not in the repository). An update installs over the previous version only if it is signed with
the same key, so keep that folder.
"""
import os
import secrets
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
KEYS = HERE / "keystore"
MIN_SDK, TARGET_SDK = "26", "34"
VERSION_CODE, VERSION_NAME = "1", "1.0"
BUILD_TOOLS, PLATFORM = "34.0.0", "android-34"


def java_home() -> Path:
    candidates = [os.environ.get("JAVA_HOME")]
    candidates += sorted(Path(r"C:\Program Files\JetBrains").glob("*/jbr"), reverse=True)
    for home in filter(None, candidates):
        if (Path(home) / "bin" / "javac.exe").exists() or (Path(home) / "bin" / "javac").exists():
            return Path(home)
    raise SystemExit("No JDK found: set JAVA_HOME to a JDK 17 or newer")


def run(*command) -> None:
    command = [str(c) for c in command]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise SystemExit(f"FAILED: {' '.join(command[:3])} ...\n{result.stdout}\n{result.stderr}")
    noise = ("warning: [options]", "Note:", "1 warning", "2 warnings", "3 warnings")
    for line in (result.stdout + result.stderr).splitlines():
        if line.strip() and not line.strip().startswith(noise):
            print("   ", line)


def main() -> None:
    sdk = Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "tmp" / "android-sdk")
    tools = sdk / "build-tools" / BUILD_TOOLS
    android_jar = sdk / "platforms" / PLATFORM / "android.jar"
    if not android_jar.exists() or not (tools / "aapt2.exe").exists():
        raise SystemExit(f"SDK packages are missing in {sdk}: run fetch_sdk.py first")
    jdk = java_home()
    java, javac, keytool = (jdk / "bin" / name for name in ("java", "javac", "keytool"))

    shutil.rmtree(BUILD, ignore_errors=True)
    (BUILD / "gen").mkdir(parents=True)
    (BUILD / "classes").mkdir()
    (BUILD / "dex").mkdir()

    print("resources")
    run(tools / "aapt2", "compile", "--dir", HERE / "res", "-o", BUILD / "res.zip")
    unsigned = BUILD / "unsigned.apk"
    run(tools / "aapt2", "link", "-o", unsigned, "-I", android_jar, "--manifest", HERE / "AndroidManifest.xml",
        "--java", BUILD / "gen", "--min-sdk-version", MIN_SDK, "--target-sdk-version", TARGET_SDK,
        "--version-code", VERSION_CODE, "--version-name", VERSION_NAME, BUILD / "res.zip")

    print("java")
    sources = [*HERE.glob("src/**/*.java"), *BUILD.glob("gen/**/*.java")]
    # core-lambda-stubs.jar lets javac compile lambdas against the Android platform; d8 turns them into classes.
    boot = os.pathsep.join(map(str, (tools / "core-lambda-stubs.jar", android_jar)))
    run(javac, "-encoding", "UTF-8", "-source", "8", "-target", "8", "-bootclasspath", boot,
        "-d", BUILD / "classes", *sources)

    print("dex")
    run(java, "-cp", tools / "lib" / "d8.jar", "com.android.tools.r8.D8", "--release", "--lib", android_jar,
        "--min-api", MIN_SDK, "--output", BUILD / "dex", *BUILD.glob("classes/**/*.class"))
    with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED) as apk:
        apk.write(BUILD / "dex" / "classes.dex", "classes.dex")

    print("align and sign")
    aligned = BUILD / "aligned.apk"
    run(tools / "zipalign", "-f", "-p", "4", unsigned, aligned)
    KEYS.mkdir(exist_ok=True)
    store, secret = KEYS / "release.jks", KEYS / "password.txt"
    if not store.exists():
        secret.write_text(secrets.token_urlsafe(24), encoding="ascii")
        run(keytool, "-genkeypair", "-keystore", store, "-storepass:file", secret, "-keypass:file", secret,
            "-alias", "equipvision", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", "CN=EquipVision")
    apk = BUILD / "EquipVision.apk"
    # the key has the password of the store, so apksigner is given the file once
    run(java, "-jar", tools / "lib" / "apksigner.jar", "sign", "--ks", store, "--ks-pass", f"file:{secret}",
        "--ks-key-alias", "equipvision", "--out", apk, aligned)
    run(java, "-jar", tools / "lib" / "apksigner.jar", "verify", "--verbose", "--min-sdk-version", MIN_SDK, apk)
    print(f"built {apk} ({apk.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
