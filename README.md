# TL-UGC-Editor
> Replace TL:LDR custom item textures using PNGs no in-game drawing or mods required.

A simple Python-based GUI editor for **Tomodachi Life: Living the Dream** User Generated Content (UGC).

This tool lets you open your game save, preview custom items made in the Palette House, and replace them with PNG images without the need to draw them in-game or use mods. Should work on any platform that allows you to export your game saves as folders or zip files.

**Tested with: Eden emulator on Android running TL:LDR v1.0.1, proven to work with Food, Items, and Clothing so far. Feel free to test the rest out!**

---

### How it works

Custom items in TL:LDR are stored inside your save as compressed texture files:


```
Ugc/
├── UgcXXXX.canvas.zs       (Base image/layout)
├── UgcXXXX.ugctex.zs       (Main texture)
└── UgcXXXX_Thumb.ugctex.zs (Thumbnail)
```

This script looks for the `Ugc` folder inside your save and detects all custom items.

These items are stored as Zstandard (`.zs`) compressed textures. The tool:
- decompresses them into viewable images
- allows you to replace them with a PNG
- re-encodes and recompresses them into the correct formats
- writes everything back into a new edited save
---

## How to use TL-UGC-Editor

### Installation

Install the dependenvies:
```
pip install pillow zstandard
```
Optional for drag-and-drop
```
pip install tkinterdnd2
```
Download the sourcecode zip file above or the latest release and run the script
```
cd .\TL-UGC-Editor
python .\tl_ugc_editor.py
```

### Usage

1. In your game, create a new object in the Palette House with all of the variables you want, draw over it however you'd like to mark it then save it  
2. Export your save as a `.zip` file. Alternatively you can attempt locating your game's save files in your emulator's/jailbroken switch's files and copying them over  
3. Run TL-UGC-Editor  
4. Load your save folder/ZIP
5. In the list on the left side, select the items to see a preview of them  
6. Select your item of choice  
7. Choose **"Replace with PNG"** and select your PNG of choice  
8. Save as `.zip`  
9. Load your save and enjoy your newly edited custom items!

- For replacing the textures with a PNG, there are 3 options to choose from for the fit: `Contain`, `Cover`, and `Stretch`. There are also some backgrounds options for the previews.

- Keep in mind that selecting the main object under the category tree and choosing the option "Replace with PNG" will replace all texture elements with the PNG you pick, if you're aiming to change them individually to have a different image for each, open the drop-down and select the individual texture you want to change then replace with PNG.

## This tool ONLY edits the UGC textures in your saves, however, I am NOT responsible for any lost or broken saves. Please back up your saves before using this tool.
