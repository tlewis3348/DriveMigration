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
  * [X] **Capture Partial Metadata**: Fix issues with "2008.4.D32 Sexual Morality and Its Implications for Art - jimberg.com.pdf" and "1934.2.D56 Sex and Culture - Unwin.pdf" not being parsed correctly: Ensure filenames with partial metadata are not ignored as though they are missing all metadata.
  * [X] **Handle Special Cases for Non-Static Files**: Handle special cases for word processing documents, presentations, and spreadsheets (i.e., non-static files): Use the "Document" type in Zotero. Filenames should remain as is with no changes, and metadata should not be attempted to be extracted from the filename.
  * [X] **Sequence Number Fallback**: When a prefix is missing, but the filename begins with a number, use that number as the sequence number in the placeholder prefix (e.g., "0000.0.003").
  * [X] Move the Zotero and OneDrive links to the end of the Markdown map item listing.
  * [X] **Handle cases for Bible prefixes**: The "00:00-00:00" prefix on a document identifying the Bible passage being addressed is redundant since the hierarchy identifies the passage. Therefore, the prefix should be removed from the filename and the filename should be addressed with the standard algorithm.
  * [ ] **Implement Immutable Target-ID Anchoring for Folder Shortcuts in `drive_migr2.py`**:
    * [ ] Create a `self.materialized_folders` tracking set to log folder IDs only when processed at their authentic, primary parental coordinates.
    * [ ] Update the directory traversal loop to intercept `application/vnd.google-apps.shortcut` objects targeting directories.
    * [ ] Force the engine to write a predictable Markdown cross-reference link (`[Shortcut Name](#folder-targetId)`) immediately upon encounter, bypassing downstream traversal.
    * [ ] Prevent shortcuts from dynamically materializing files or duplicate folder structures out of their true taxonomic context, ensuring absolute path uniqueness and protection against infinite cyclic recursion.
    * [ ] Append a unique HTML anchor tag (`<a name="folder-currentId"></a>`) to every true folder list item written to `My_Life_and_Worldview.md` to serve as the invariant target destination for current and future cross-links.
* [ ] **Full Functionality Testing**: After confirming the map structure, run the full migration on a small subset of files to ensure that the API interactions, checkpointing, and deduplication logic work as expected.
* [ ] **File Statistics**: After migration, generate a report of migrated files, including counts of new Zotero items created, existing items updated, and any files that were skipped due to deduplication.
* [ ] **Distinguish Between File Owners**: If the file is a Google Docs, Sheets, or Slides format that I created, don't modify the filename. If it is a file created by someone else, apply the same renaming logic.

# Version 3 Features

## 1. Relative Focus & Scaling Layer (Focus + Context Hyperbolic View)
- [ ] **Implement Focus-Centric Dynamic Scaling Mechanics**: Use a relative layout matrix that re-centers and scales the canvas geometry dynamically based on the actively selected node (Focus Node rendered at 100% scale and maximum visual emphasis).
- [ ] **Construct a Topological Distance Evaluator**: Build an in-memory graph traversal routine that calculates the shortest path steps ($D$) from the currently focused node to all other elements across the Directed Acyclic Graph (DAG).
- [ ] **Develop Relative Level-of-Detail (LoD) Scaling Rules**:
  - [ ] **Active Ancestral Path Tiers ($D$ along active parent chains)**: Render all direct upward lines of parentage back to the root categories at a uniform, highly legible scale to maintain instant multi-context visibility.
  - [ ] **Adjacent Local Branches ($D = 1$ to $3$ off active paths)**: Step down font sizes, line weights, and node boundaries using a decaying exponential modifier ($Scale \propto e^{-D}$) as nodes drift away from the active line of sight.
  - [ ] **Periphery Global Framework ($D \ge 4$ off active paths)**: Compress the remaining 10,000+ un-focused global nodes into microscopic, low-opacity spatial clusters at the margins of the screen to preserve permanent, concurrent global system context without text layout collision penalties.

## 2. Advanced Topological Topography (Multi-Parent Ancestry & Relational Vectors)
- [ ] **Implement Multi-Parent Structural Branch Convergence**: Enforce multi-parent graph routing at the visualization layer. When an asset or textual node is claimed by multiple parent tracks, the layout engine must converge those discrete paths down to a single physical node on the screen, showing the item executing co-equal membership in multiple hierarchies simultaneously.
- [ ] **Engineer Focus-Driven Vector Path Highlighting**:
  - [ ] Program an interactive rendering subroutine that tracks hover or touch selections on multi-parent items.
  - [ ] Ensure that selecting a multi-parent node instantly illuminates high-contrast directional vector lines tracing upward through all its concurrent parental tracks back to the root macro categories, while dropping irrelevant collateral connections into deep shadow.
- [ ] **Integrate Protocol Deep-Linking**: Bind touch and click interactions directly to node boundaries. Tapping a visible research node must execute a native system call to trigger local application handlers (e.g., `zotero://select/...` or your local OneDrive physical file paths) directly from the desktop or mobile device interface.

## 3. Responsive Multi-View Architecture (Desktop Panes vs. Mobile Stack)
- [ ] **Implement View-Separation for High-Density Metadata**: Completely decouple individual file asset listings and metadata profiles from the graphic tree pipeline to maximize map readability.
- [ ] **Code Responsive Layout Adapters**:
  - [ ] **Desktop Target Configuration**: Render a synchronized three-pane workspace configuration when widescreen metrics are detected (Left pane: Hyperbolic Map; Middle Pane: Item List; Right Pane: Metadata Details).
  - [ ] **Mobile Target Configuration**: Enforce a full-screen Viewport Stack mechanism on phone displays (Map occupies full screen; tapping nodes anchors a swipe-up Bottom Sheet for the Item List; selecting items slides a dedicated full-screen view into place for individual Zotero details).
- [ ] **Establish Multi-View State Synchronization**: Ensure that executing a node selection or focus adjustment in one viewport instantly pushes state changes down to update the adjacent list and detail containers across both platforms.

# Version 4 Features

## 1. Bi-Directional Zotero Read/Write Frontend
* [ ] **Implement Interactive Collection Creation**: Build tools directly into the Canvas UI to allow the generation of new Zotero collections by right-clicking or long-pressing on the coordinate map.
* [ ] **Build Item Modification Windows**: Construct data-entry fields to create parent Zotero items, attach files, and modify standard bibliographic metadata keys (Author, Title, Date, Publication) directly through the product frontend.
* [ ] **Design API Delta Synchronization**: Code an asynchronous network layer that pushes local modifications directly to the Zotero Web API and immediately updates your local data stream without requiring a full library rebuild.

## 2. Provider Agnosticism & Template Customization
* [ ] **Abstract Cloud Storage Access Layers**: Decouple the data attachment logic from Microsoft Graph. Build interface adapters to allow users to select from a wider array of storage backends (e.g., local hard drives, Proton Drive, Nextcloud, or standard SFTP servers).
* [ ] **Implement a Dynamic Filename Template Engine**: Create a configuration parser that allows users to write custom formatting strings (e.g., [Prefix] [Author] - [Title]) using the metadata dictionary. The engine must automatically rename physical files and update Zotero attachments globally based on the active template rules.

## 3. Merge Textual Content with Map Structure (for Bible Content)
- [ ] Implement a Polymorphic Layout Engine inside the Phase B Canvas UI that enforces a strict three-tier object architecture: (1) Hierarchical Levels/Containers, (2) Invariant Text, and (3) Attached Files/Assets.
  - [ ] Establish an absolute visual priority rule where Text blocks natively supersede File assets on the rendering pipeline, locking them as lateral footnotes or minimized metadata indicators adjacent to the scriptural line.
  - [ ] Program a conditional coordinate axis transformation: apply a Radiant/Spatial arrangement to distribute pure organizational container nodes compactly across the 2D plane, but instantly force a Strict Linear/Indented layout axis for any sub-tree containing textual content to preserve sequential reading comprehension down to micro-grammatical syntax tiers.

