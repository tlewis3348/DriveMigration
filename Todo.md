# Todo list

Building a "Version 2.0" of your migration and research engine is an excellent opportunity to implement the **Session-based** and **Atomic** patterns discussed. Since you are starting from scratch, you can architect the script to be more modular and resilient for a 20-year library.

Here is the prioritized TODO list for your new implementation, organized by architectural layer.

## 1. Foundation: The `TransferSession` Class
Instead of passing a long list of individual arguments, encapsulate your state. This is the "Sr. Principal Analyst" approach to clean code.
* [X] Define a `TransferSession` class to hold the Google Resource, MSAL token, Zotero Requests Session, and the Checkpoint dictionary.
* [X] Initialize the **Zotero Session** with a default timeout and global headers (`Zotero-API-Key`).
* [X] Implement a `save_state()` method within the class that writes both the `checkpoint.json` and the `zotero_mapping.json` (collections) to disk atomically.

## 2. The Data Layer: Metadata & Zotero
This is where you handle the "Relationist" documentation of your worldview research.
* [X] **Refined Metadata Extractor**: Rewrite your regex as a standalone utility that returns a dictionary (Title, Author, Date, Bible Ref, Source).
* [X] **Atomic Zotero Logic**: Create a function `get_or_create_research_item()` that:
    * [X] Searches Zotero by title first.
    * [X] If it exists: Moves it to the new collection and **replaces** the Google Drive attachment with the OneDrive URL.
    * [X] If it doesn't: Creates the parent and the linked attachment in a single logical flow.
* [X] **Rate Limiter**: Add a decorator or a small helper to ensure every Zotero API call is followed by a `time.sleep(0.5)` to avoid throttling on large batches.

## 3. The Visual Layer: Freeplane XML
This layer handles the "Google Earth" zooming effect by generating a valid `.mm` file structure.
* [x] **Object-Oriented Map Engine**: Encapsulate the XML generation inside a `FreeplaneMap` class with a nested `MapNode` object to cleanly track parent-child hierarchical relationships.
* [x] **Attribute-Based Node Depth**: Have the `MapNode.add_child()` method automatically calculate integer depth, and the `render()` method inject the XML `<attribute NAME="Depth" VALUE="x"/>`.
    * This allows you to control the "fade into obscurity" entirely within Freeplane using its **Conditional Styles** engine later.
* [x] **Link Prioritizer**: Create a static helper (`get_best_link`) that determines the "Best Link" for a node before attachment (Priority: Zotero URI > OneDrive webUrl).

## 4. Preparation: Checkpointing & Deduplication
* [X] **Define Hashing & Content Mapping State**: Section 4 requires a `content_map` to handle global deduplication. You need to initialize a `self.content_map: Dict[str, str] = {}` property in `TransferSession.__init__` to store file hashes (`md5Checksum` from Google Drive metadata) and map them to their uploaded OneDrive URLs.
* [X] **Define Name Tracking Pools (`used_names`)**: To prevent filesystem name collisions when multiple different Google Drive documents truncate down to the exact same Windows filename, your upcoming file processing logic will need an in-memory tracking set (`self.used_names: Set[str] = set()`) to handle unique index validation.

## 5. The API Service Layer (The Physical Transport)
Decouple API-specific network logic from the main `TransferSession` state manager into dedicated service classes to prevent a "God Object" architecture.
* [X] **Google Drive Client**: Extract Google-specific methods into a new class (e.g., `GoogleDriveClient`).
    * The `GoogleDriveClient` would be responsible for all interactions with the Google Drive API, including authentication, fetching folder contents, and downloading files. This separation allows Google-specific logic to be isolated and makes it easier to maintain or swap out the Google Drive API in the future if needed.
* [X] **Microsoft Graph / OneDrive Client**: Extract Microsoft-specific methods into a new class (e.g., `OneDriveClient`).
    * The `OneDriveClient` would handle all interactions with the Microsoft Graph API, including authentication, file uploads (including chunked uploads), and any other OneDrive-specific operations. This separation ensures that Microsoft-specific logic is contained within its own class, improving code organization and maintainability.
* [X] **Zotero Database Client**: Extract Zotero API networking into a new class (e.g., `ZoteroClient`).
    * The `ZoteroClient` would manage all interactions with the Zotero API, including creating/updating items, managing collections, and handling attachments. This separation allows for a clear distinction between Zotero-specific logic and the overall session management, making the codebase cleaner and more modular.
* [X] **Session Integration**: Update `TransferSession.__init__` to instantiate these three clients, passing them the necessary environment variables/credentials, so the session acts purely as the central orchestrator.

## 6. The Engine: Recursive Traversal
This is the heart of the script. It utilizes the API Service Layer to execute physical actions and the orchestrator to track state.
* [X] **The Traversal Controller (`process_folder`)**: Create a recursive method that acts purely as a traffic director.
    * Query the Google Drive folder name and resolve its Zotero collection via `self.sync_collection()`.
    * Build the visual folder node in Freeplane (`parent_node.add_child()`).
    * Fetch children using `self.gdrive.get_children()` and route sub-folders back into `process_folder()`, while routing standard files to `process_file()`. (Ensure non-exportable Google formats like Forms/Sites are skipped).
* [X] **The Atomic Data Transport (`process_file`)**: Create a discrete file processing method to handle download, upload, and metadata syncing.
    * **Checkpoint Defense**: Immediately skip processing and restore the Freeplane node from cache if the file ID already exists in `self.checkpoint`.
    * **Smart Deduplication**: Check the native `md5Checksum` from the Google Drive payload against `self.content_map` *before* downloading. If it is a Workspace file (no native hash), download it first, then use `self.calc_stream_hash()`.
    * **Namespace Integrity**: Call `self.unique_name()` to calculate the exact Windows-safe string, guaranteeing no collisions via `self.used_names`.
    * **Physical & Relational Sync**: Push the bytes to `self.onedrive.upload_file()` (if not bypassed by deduplication) and generate the Zotero metadata via `self.sync_item()`.
    * **Checkpoint Integrity**: Append the success record to `self.checkpoint` and call `self.save_state()` *only at the very end* to prevent "half-migrated" states.

## 7. Final Assembly: The Main Flow
* [X] **Execution Block**: Create the standard `if __name__ == "__main__":` block at the bottom of the script.
    * Initialize the `TransferSession`.
    * Build the local Zotero memory map (`session.build_index()`).
    * Define the Google Drive root folder ID and call `session.process_folder()`.
    * Save the final Freeplane map XML to disk when the traversal completes.
* [X] Implement a "Dry Run" flag in your config. This allows you to test the hierarchy generation in Freeplane without actually uploading files or calling the Zotero API.

## 8. Additional Features
* [X] **Ephemeral Deduplication Memory (`content_map` Amnesia)**: Add self.`content_map_file = "content_map.json"` to your `__init__`. Load it dynamically in `_load_state()` and write it to disk in `save_state()`, exactly as you did with `checkpoint.json`. This ensures that your deduplication logic persists across runs, preventing re-uploads of the same file if you need to restart the script.
* [X] **Handle Google Drive Shortcuts**: Implement logic to detect and resolve Google Drive shortcuts during traversal. This may involve checking the `shortcutDetails` field in the Drive API response and deciding whether to follow the shortcut or treat it as a regular file/folder. Make use of the fact that items in Zotero can be included in multiple collections to handle cases where the same Google Drive file is linked from different folders.
* [X] **Microsoft Graph Token Expiration**: Implement a dynamic token refresh mechanism in `OneDriveClient` to handle long-running migrations that may exceed the token's lifespan.
* [X] **Enhanced Logging**: Integrate Python's `logging` module to provide more granular control over log levels (INFO, DEBUG, ERROR) and output formats. This will help you track the migration process more effectively, especially when dealing with large batches of files.

## 9. Post-Migration: Freeplane Setup
* [ ] **Define Conditional Styles**: Once the script runs, open Freeplane and create rules like: *"If Attribute 'Depth' > 3, then set Font Size = 8pt and Opacity = 40%"*. This completes your vision for the "Semantic Zoom."

# Future Improvements

The following critical evaluation identifies opportunities to enhance your script's architecture, specifically focusing on **atomic data integrity**, **concise API handling**, and **visual clarity** for your Freeplane map.

## 1. Architectural Improvements for Flow & Readability

* [X] **Session Lifecycle Management**:
    While you are using a `ZOTERO_SESSION`, you are still creating new `msal.PublicClientApplication` and Google `service` objects inside `main`. Moving these into a unified `TransferSession` class would allow you to pass a single object through your `traverse` function rather than a growing list of individual arguments, greatly improving readability.
* [X] **Decouple Traversal from Logic**:
    Currently, `traverse` handles recursion, API calls, checkpointing, and XML generation in one large block. Extracting the "Process File" logic into a separate function would make the flow easier to follow and simplify debugging.

## 2. Functional Enhancements for Zotero and OneDrive

* [X] **Zotero Metadata Mapping**:
    Your current regex pattern in `safe_name` is sophisticated, but it isn't being used in `create_or_update_zotero_entry` to populate Zotero fields like `date`, `creators`, or `extra`. Passing the parsed metadata dict into the Zotero function would ensure your documented items are as rich as your filenames.

## 3. Conciseness and Styling in Freeplane

* [X] **Attribute-Based Styles**:
    Instead of manually setting `STYLE="RootTopic"` based on depth, you can simply set a `Depth` attribute on every node: `node.set("DEPTH", str(depth))`. This allows you to use Freeplane’s **Conditional Styles** feature to manage the "fade into obscurity" effect globally without hard-coding specific style names in Python.
* [X] **Unified Link Logic**:
    You have redundant logic for determining if a link should be a `zot_uri` or a `webUrl` in multiple places. Consolidating this into a helper function like `get_best_link(entry)` would make the code more concise.

## [X] Suggested Code Refinements

```python
# Refinement: Global Metadata Extractor
def extract_research_metadata(filename: str) -> Dict[str, Any]:
    # Extract YYYY.Q, Author, Title, and Bible Ref from your specific pattern
    pattern = r'^(\d{4}\.\d\.[D\d]\d+|\d+\.\d+-\d+\.\d+)\s+(.+?)\s+-\s+([^,]+),\s+(.+)$'
    match = re.match(pattern, filename)
    if not match:
        return {"title": filename}
    prefix, title, author, source = match.groups()
    return {"title": title, "date": prefix.split('.')[0], "author": author, "extra": source}

# Refinement: Unified Node Adder
def add_to_freeplane(parent_xml: ET.Element, name: str, link: str, depth: int) -> ET.Element:
    node = ET.SubElement(parent_xml, "node", TEXT=name)
    if link and link != "NONE":
        node.set("LINK", link)
    # Use Attributes for scaling rather than hard-coded styles
    ET.SubElement(node, "attribute", NAME="Depth", VALUE=str(depth))
    return node
```

## Summary Evaluation
The script is exceptionally well-structured for a complex migration. By moving toward a **class-based session** and **attribute-based Freeplane styling**, you will achieve a "cleaner" flow that is easier to maintain as your 20-year library continues to grow.

This visualization confirms that your current `depth` logic is the correct way to handle a "Google Earth" style zoom within a deterministic XML hierarchy. Should you implement the **Zotero Metadata Mapping** mentioned above, your "Details" level in Freeplane will become significantly more informative.

# Debugging & Testing
* [ ] **Dry Run Testing**: Run migration tool in the "dry run" mode to validate the map generation without making actual API calls. This allows you to verify the structure and metadata extraction logic.
* [ ] **Full Functionality Testing**: After confirming the map structure, run the full migration on a small subset of files to ensure that the API interactions, checkpointing, and deduplication logic work as expected.

# Version 3 Features

## 1. Spatial Multi-Scale Rendering Layer ("Google Maps" UI)
* [ ] **Implement Infinite Pan and Zoom Mechanics**: Code a 2D camera matrix transform layer that interprets trackpad scrolls, mouse drags, and mobile touch gestures (pinch-to-zoom) into deterministic coordinates on an infinite mathematical grid.
* [ ] **Construct a Quadtree Spatial Index**: Build an in-memory Quadtree data structure to host every visual element coordinate. Panning the map must trigger localized bounding-box queries (O(log N) complexity) to ensure that only nodes within the viewport boundary are passed to the draw loop, allowing 60 FPS performance on mobile devices.
* [ ] **Develop Level-of-Detail (LoD) Progressive Disclosure**:
  * [ ] **Zoom Level 0–2 (Global Category View)**: Display only the macro structural worldview categories (e.g., 01 Theory, 02 Practice) as large, high-contrast anchor hubs. Sub-branches are completely culled from the drawing queue to conserve memory.
  * [ ] **Zoom Level 3–6 (Regional Sub-Category View)**: Fade mid-level structural folders (e.g., Epistemology, Apologetics) into view as the camera descends. Deep text strings remain hidden.
  * [ ] **Zoom Level 7+ (Street Level Detail)**: Render the fine details—individual research item filenames, clickable attachment links, and status tags—at full opacity.

## 2. Advanced Topological Topography (Multi-Parent & Relational Proximity)
* [ ] **Implement Multi-Parent Graph Node Architecture**: Break away from standard tree constraints to allow Graph structures. If a single Zotero item exists in multiple collections (or is targeted by multiple Google Drive shortcuts), the renderer must map incoming directional vectors from all parent nodes to that single physical coordinate point.
* [ ] **Engineer Cross-Collection Proximity Anchoring ("Geographic Closeness")**:
  * [ ] Establish an invisible relational weight configuration between distinct collections that reference one another.
  * [ ] Write a force-directed layout layout subroutine that pulls cross-referenced collections geographically closer to one another on the coordinate map, even if no visible hierarchical lines connect them in the viewport.
* [ ] **Integrate Protocol Deep-Linking**: Bind touch and click interactions directly to node boundaries. Tapping a visible research node must execute a native system call to trigger the local application handlers (zotero://select/... or your local OneDrive file paths) directly from the desktop or mobile device.

# Version 4 Features

## 1. Bi-Directional Zotero Read/Write Frontend
* [ ] **Implement Interactive Collection Creation**: Build tools directly into the Canvas UI to allow the generation of new Zotero collections by right-clicking or long-pressing on the coordinate map.
* [ ] **Build Item Modification Windows**: Construct data-entry fields to create parent Zotero items, attach files, and modify standard bibliographic metadata keys (Author, Title, Date, Publication) directly through the product frontend.
* [ ] **Design API Delta Synchronization**: Code an asynchronous network layer that pushes local modifications directly to the Zotero Web API and immediately updates your local data stream without requiring a full library rebuild.

## 2. Provider Agnosticism & Template Customization
* [ ] **Abstract Cloud Storage Access Layers**: Decouple the data attachment logic from Microsoft Graph. Build interface adapters to allow users to select from a wider array of storage backends (e.g., local hard drives, Proton Drive, Nextcloud, or standard SFTP servers).
* [ ] **Implement a Dynamic Filename Template Engine**: Create a configuration parser that allows users to write custom formatting strings (e.g., [Prefix] [Author] - [Title]) using the metadata dictionary. The engine must automatically rename physical files and update Zotero attachments globally based on the active template rules.
