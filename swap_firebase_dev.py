"""
swap_firebase_dev.py
Run this after copying soslogs_index.html to the dev branch.
It replaces the production Firebase config with the dev config.

Usage:
  python3 swap_firebase_dev.py

Place this file in your soslogs repo root directory.
"""

import re

# ── Dev Firebase config — update these values once ───────────
# Get from Firebase Console -> soslog-v1-dev -> Project Settings -> Your apps -> Config
DEV_CONFIG = {
    "apiKey":            "PASTE_DEV_API_KEY_HERE",
    "authDomain":        "soslog-v1-dev.firebaseapp.com",
    "projectId":         "soslog-v1-dev",
    "storageBucket":     "soslog-v1-dev.appspot.com",
    "messagingSenderId": "PASTE_DEV_SENDER_ID_HERE",
    "appId":             "PASTE_DEV_APP_ID_HERE",
}

# ── Production Firebase config — for reference only ──────────
PROD_IDENTIFIERS = [
    "soslog-v1.firebaseapp.com",
    '"soslog-v1"',
    "soslog-v1.appspot.com",
    "soslog-v1.firebasestorage.app",
]

def swap_config(filename="index.html"):
    with open(filename, "r", encoding="utf-8") as f:
        content = f.read()

    # Check if already on dev config
    if DEV_CONFIG["projectId"] in content:
        print("✅ Already using dev Firebase config — no change needed.")
        return

    # Find and replace the initializeApp config block
    pattern = r'(const fbApp\s*=\s*initializeApp\s*\(\s*\{)(.*?)(\}\s*\))'
    
    new_config_str = """
  apiKey:            "{apiKey}",
  authDomain:        "{authDomain}",
  projectId:         "{projectId}",
  storageBucket:     "{storageBucket}",
  messagingSenderId: "{messagingSenderId}",
  appId:             "{appId}"
""".format(**DEV_CONFIG)

    def replace_config(match):
        return match.group(1) + new_config_str + match.group(3)

    new_content, count = re.subn(pattern, replace_config, content, flags=re.DOTALL)

    if count == 0:
        print("❌ Could not find initializeApp config block. Check the pattern.")
        return

    with open(filename, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ Swapped Firebase config to dev ({DEV_CONFIG['projectId']})")
    print(f"   authDomain: {DEV_CONFIG['authDomain']}")
    print(f"   projectId:  {DEV_CONFIG['projectId']}")

if __name__ == "__main__":
    swap_config()
    print("\nNext steps:")
    print("  git add index.html")
    print('  git commit -m "Update dev Firebase config"')
    print("  git push origin dev")
