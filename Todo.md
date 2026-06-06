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
* [ ] **Session Integration**: Update `TransferSession.__init__` to instantiate these three clients, passing them the necessary environment variables/credentials, so the session acts purely as the central orchestrator.

## 6. The Engine: Recursive Traversal
This is the heart of the script.
* [ ] **Decoupled Folder/File Logic**:
    * The `traverse()` function should only handle the recursion and folder creation.
    * Move the "File Processing" (download, upload, zotero, xml) into a separate function called `process_file()`.
* [ ] **Global Deduplication**: Before uploading any file, check the `content_map` (hashed IDs). If a match is found, create a "Shortcut Node" in Freeplane that points to the original Zotero URI.
* [ ] **Checkpoint Integrity**: Ensure the checkpoint is updated *only after* both the OneDrive upload and Zotero documentation are successful. This prevents "half-migrated" entries.

## 7. Final Assembly: The Main Flow
* [ ] Implement a "Dry Run" flag in your config. This allows you to test the hierarchy generation in Freeplane without actually uploading files or calling the Zotero API.
* [ ] Setup the `root_map` with the correct Freeplane XML version and a single central "Worldview" node.

## 8. Post-Migration: Freeplane Setup
* [ ] **Define Conditional Styles**: Once the script runs, open Freeplane and create rules like: *"If Attribute 'Depth' > 3, then set Font Size = 8pt and Opacity = 40%"*. This completes your vision for the "Semantic Zoom."

# Future Improvements

The following critical evaluation identifies opportunities to enhance your script's architecture, specifically focusing on **atomic data integrity**, **concise API handling**, and **visual clarity** for your Freeplane map.

## 1. Architectural Improvements for Flow & Readability

* **Session Lifecycle Management**:
    While you are using a `ZOTERO_SESSION`, you are still creating new `msal.PublicClientApplication` and Google `service` objects inside `main`. Moving these into a unified `TransferSession` class would allow you to pass a single object through your `traverse` function rather than a growing list of individual arguments, greatly improving readability.
* **Decouple Traversal from Logic**:
    Currently, `traverse` handles recursion, API calls, checkpointing, and XML generation in one large block. Extracting the "Process File" logic into a separate function would make the flow easier to follow and simplify debugging.

## 2. Functional Enhancements for Zotero and OneDrive

* **Atomic Zotero Operations**:
    Your `create_or_update_zotero_entry` still performs multiple sequential requests (Search -> Get Children -> Delete -> Post). To make the flow smoother, you can use Zotero’s **Write Actions** or batch processing to reduce the risk of a partial update if your connection drops mid-process.
* **Path-Aware Deduping**:
    Your `content_map` currently dedupes based solely on the `underlyingId`. For a "Relationist" worldview, you might want to allow the same content to appear in different branches if it serves a different logical purpose. Adding a flag to toggle between "Global Deduping" and "Branch-Specific Deduping" would improve functionality.
* **Zotero Metadata Mapping**:
    Your current regex pattern in `safe_name` is sophisticated, but it isn't being used in `create_or_update_zotero_entry` to populate Zotero fields like `date`, `creators`, or `extra`. Passing the parsed metadata dict into the Zotero function would ensure your documented items are as rich as your filenames.

## 3. Conciseness and Styling in Freeplane

* **Attribute-Based Styles**:
    Instead of manually setting `STYLE="RootTopic"` based on depth, you can simply set a `Depth` attribute on every node: `node.set("DEPTH", str(depth))`. This allows you to use Freeplane’s **Conditional Styles** feature to manage the "fade into obscurity" effect globally without hard-coding specific style names in Python.
* **Unified Link Logic**:
    You have redundant logic for determining if a link should be a `zot_uri` or a `webUrl` in multiple places. Consolidating this into a helper function like `get_best_link(entry)` would make the code more concise.

## Suggested Code Refinements

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
