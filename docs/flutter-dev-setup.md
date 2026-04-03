# Flutter Development Environment Setup
**macOS Sequoia 15.7.x | Intel Mac (darwin-x64)**

---

## Prerequisites

- macOS Sequoia 15.6 or later
- [Homebrew](https://brew.sh/) installed
- Free Apple Developer account (for Xcode download)

---

## 1. Install Flutter

If not already installed, follow the official guide:
https://docs.flutter.dev/get-started/install/macos

Verify installation:
```bash
flutter doctor
```

Disable analytics (optional):
```bash
flutter --disable-analytics
```

---

## 2. Install Xcode

> ⚠️ **Do not install Xcode from the App Store.** Download directly from the Apple Developer portal for reliability.

### Compatibility note
- **Xcode 26.4** requires macOS Tahoe 26.2 — not compatible with Sequoia
- **Xcode 16.4** requires macOS Sequoia 15.6 — ✅ compatible

### Steps

1. Go to https://developer.apple.com/download/all/
2. Search for **Xcode 16** and download the `.xip` for your architecture
3. Wait for extraction to complete (happens automatically after download)
4. Drag `Xcode.app` into `/Applications`

### Set Xcode path

```bash
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -runFirstLaunch
sudo xcodebuild -license accept
```

Verify:
```bash
xcode-select -p
# Expected output: /Applications/Xcode.app/Contents/Developer
```

### Install CocoaPods

```bash
brew install cocoapods
```

### Download iOS Simulator

```bash
xcodebuild -downloadPlatform iOS
```

This downloads the latest iOS Simulator runtime (~8.8 GB). Alternatively, open **Xcode → Settings → Platforms** and download from there.

---

## 3. Install Android Toolchain (Command Line Only)

No Android Studio required.

### 3a. Download Android Command Line Tools

1. Go to https://developer.android.com/studio#command-line-tools-only
2. Download the macOS zip

### 3b. Set up the SDK directory

```bash
mkdir -p ~/android-sdk/cmdline-tools
unzip commandlinetools-mac-*.zip -d ~/android-sdk/cmdline-tools
mv ~/android-sdk/cmdline-tools/cmdline-tools ~/android-sdk/cmdline-tools/latest
```

### 3c. Add to PATH

Add the following to `~/.zshrc`:

```bash
export ANDROID_SDK_ROOT=$HOME/android-sdk
export PATH=$PATH:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin
export PATH=$PATH:$ANDROID_SDK_ROOT/platform-tools
```

Reload your shell:

```bash
source ~/.zshrc
```

### 3d. Install Java (required by sdkmanager)

```bash
brew install --cask temurin
```

### 3e. Install Android SDK packages

```bash
sdkmanager --install "platform-tools" "platforms;android-36" "build-tools;36.0.0"
```

> **Note:** `platforms;android-35` was the latest stable at time of writing. Flutter may require a newer version — check `flutter doctor` output and install accordingly.

### 3f. Accept Android licenses

```bash
flutter doctor --android-licenses
```

Press `y` at each prompt.

---

## 4. Verify Full Setup

```bash
flutter doctor
```

Expected output:
```
[✓] Flutter
[✓] Android toolchain
[✓] Xcode
[✓] Chrome
[✓] Connected device
[✓] Network resources
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `Xcode.app` not in `/Applications` after download | Manually drag extracted `.app` from Downloads into `/Applications` |
| `Unable to locate Android SDK` | Verify `ANDROID_SDK_ROOT` is set in `~/.zshrc` and sourced |
| `sdkmanager: Unable to locate a Java Runtime` | Run `brew install --cask temurin` |
| `Flutter requires Android SDK 36` | Run `sdkmanager --install "platforms;android-36" "build-tools;36.0.0"` |
| Xcode simulator missing | Run `xcodebuild -downloadPlatform iOS` |
