import os
import io
import json
import msal # type: ignore
import re
import time
import hashlib
import requests
import tempfile

from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow # type: ignore
from google.auth.transport.requests import Request
from googleapiclient.discovery import build # type: ignore
from googleapiclient.http import MediaIoBaseDownload # type: ignore
from requests.adapters import HTTPAdapter
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib3.util.retry import Retry
from urllib.parse import quote
from dotenv import load_dotenv

# region: CONFIG
OUTPUT_MM: str = "index.mm"
CHECKPOINT_FILE: str = "checkpoint.json"
COLLECTION_MAP_FILE: str = "zotero_collections.json"
CONTENT_MAP_FILE: str = "content_map.json"

# Load environment variables
load_dotenv()
# endregion

class GoogleDriveClient:
    """Handles all authentication, traversal, and download operations for the Google Drive API."""

    # We only need read permissions for the migration
    SCOPES: List[str] = ['https://www.googleapis.com/auth/drive.readonly']

    def __init__(self, client_secrets_path: str, token_cache_path: str = "token.json"):
        """Initializes the client and builds the authenticated service."""
        if not client_secrets_path or not os.path.exists(client_secrets_path):
            raise FileNotFoundError(f"Google client secrets not found at: {client_secrets_path}")

        self.service: Any = self._authenticate(client_secrets_path, token_cache_path)

    def _authenticate(self, client_secrets_path: str, token_cache_path: str) -> Any:
        """Manages the OAuth2 handshake and local token caching."""
        creds: Optional[Credentials] = None

        # Load cached credentials if they exist
        if os.path.exists(token_cache_path):
            creds = Credentials.from_authorized_user_file(token_cache_path, self.SCOPES) # type: ignore

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token: # type: ignore
                creds.refresh(Request()) # type: ignore
            else:
                flow: InstalledAppFlow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, self.SCOPES) # type: ignore
                creds: Optional[Credentials] = flow.run_local_server(port=0) # type: ignore

            # Save the credentials for the next run
            with open(token_cache_path, 'w') as token:
                token.write(creds.to_json()) # type: ignore

        return build('drive', 'v3', credentials=creds) # type: ignore

    def get_folder_name(self, folder_id: str) -> str:
        """Queries Google Drive for the native name of a specific folder."""
        try:
            response: Dict[str, Any] = self.service.files().get(
                fileId=folder_id, 
                fields="name"
            ).execute()
            return response.get("name", "Unknown Folder")
        except Exception as e:
            print(f"Error getting folder name for {folder_id}: {e}")
            return "Unknown Folder"

    def get_children(self, folder_id: str) -> List[Dict[str, Any]]:
        """Pulls all child items (files, folders, shortcuts) within a Google Drive directory."""
        results: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        while True:
            response: Dict[str, Any] = self.service.files().list(
                q=f"'{folder_id}' in parents",
                fields="nextPageToken, files(id, name, mimeType, shortcutDetails)",
                pageToken=page_token
            ).execute()

            results.extend(response.get("files", []))

            page_token: Optional[str] = response.get("nextPageToken")
            if not page_token:
                break

        return results

    def download_file(self, file_id: str, mime_type: str, file_name: str) -> Optional[Tuple[bytes, str]]:
        """
        Downloads a file into a memory buffer. 
        Automatically converts Google Workspace formats to standard Office XML formats.
        """
        export_map: Dict[str, Dict[str, str]] = {
            "application/vnd.google-apps.document": {
                "target": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 
                "ext": ".docx"
            },
            "application/vnd.google-apps.spreadsheet": {
                "target": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                "ext": ".xlsx"
            },
            "application/vnd.google-apps.presentation": {
                "target": "application/vnd.openxmlformats-officedocument.presentationml.presentation", 
                "ext": ".pptx"
            },
            "application/vnd.google-apps.drawing": {
                "target": "image/png", 
                "ext": ".png"
            }
        }

        buffer: io.BytesIO = io.BytesIO()

        if mime_type in export_map:
            request: Any = self.service.files().export_media(fileId=file_id, mimeType=export_map[mime_type]["target"])
            ext: str = export_map[mime_type]["ext"]
            if not file_name.lower().endswith(ext):
                file_name: str = f"{file_name}{ext}"
        elif mime_type.startswith("application/vnd.google-apps."):
            print(f"Skipping non-exportable Google file: {file_name} ({mime_type})")
            return None
        else:
            request: Any = self.service.files().get_media(fileId=file_id)

        downloader: MediaIoBaseDownload = MediaIoBaseDownload(buffer, request)
        done: bool = False
        max_retries: int = 5

        while not done:
            for attempt in range(max_retries):
                try:
                    _, done = downloader.next_chunk()
                    break
                except (ConnectionResetError, Exception) as e:
                    if attempt < max_retries - 1:
                        wait_time: int = 2 ** attempt
                        print(f"Download error ({type(e).__name__}). Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        print(f"Permanent download failure after {max_retries} attempts.")
                        raise

        return buffer.getvalue(), file_name

class ResearchFileContext:
    def __init__(self, filename_limit: int = 200, extension: str = ".pdf"):
        self.limit: int = filename_limit
        self.ext: str = extension

        # Unified State Configuration representing the active file being handled
        self.raw_input: str = ""
        self.title: str = ""
        self.authors: List[str] = ["Unknown"]
        self.source: str = "Unsorted"

        # Protected internal base state
        self._base_prefix: str = "0000.0.000"

        # Public modifier state
        self.alpha_suffix: str = ""

        self.full_date: Optional[str] = None
        self.page_number: Optional[str] = None
        self.is_research_format: bool = False

        self.reserved_names: Set[str] = {
            "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
            "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
            "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
        }

    @property
    def prefix(self) -> str:
        return f"{self._base_prefix}{self.alpha_suffix}"

    def load_file_context(self, filename: str) -> None:
        """
        Ingests a raw Google Drive filename, extracts its metadata, 
        and calculates the true publication date from the filename prefix.
        """
        self.raw_input = filename
        self.alpha_suffix = ""
        name_no_ext: str = os.path.splitext(filename)[0]

        pattern: str = r"^(\d{4})\.(\d)\.([D\d]\d+)\s+(.+?)\s+-\s+([^,]+),\s+(.+)$"
        match: Optional[re.Match[str]] = re.match(pattern, name_no_ext)

        if match:
            year_str: str; q_str: str; ref: str; title: str; author_str: str; source: str
            year_str, q_str, ref, title, author_str, source = match.groups()

            self.title = title.strip()
            self.authors = [a.strip() for a in author_str.split("+")]
            self.source = source.strip()
            self._base_prefix = f"{year_str}.{q_str}.{ref}"
            self.is_research_format = True

            # REVERSE CALCULATE: Map the prefix variables back to a real ISO date
            if ref.startswith("D"):
                self.full_date = self._calculate_date_from_prefix(year_str, q_str, ref)
                self.page_number = None
            else:
                self.full_date = None
                self.page_number = ref
        else:
            self.title = name_no_ext
            self.authors = ["Unknown"]
            self.source = "Unsorted"
            self._base_prefix = "0000.0.000"
            self.full_date = None
            self.page_number = None
            self.is_research_format = False

    def load_zotero_context(self, item_data: Dict[str, Any]) -> None:
        """
        Ingests raw Zotero API metadata and maps it to the internal state,
        calculating the prefix directly from the true Zotero date properties.
        """
        self.full_date = item_data.get("date")

        pages: str = item_data.get("pages", "")
        self.page_number = pages.split("-")[0].strip() if pages else None

        # FORWARD CALCULATE: Map the ISO date to a structured YYYY.Q.REF string
        self._base_prefix = self._calculate_prefix_from_date(self.full_date, self.page_number)
        self.title = item_data.get("title", "")
        self.source = item_data.get("publisher", "")

        self.authors = []
        for creator in item_data.get("creators", []):
            if creator.get("creatorType") == "author" and creator.get("lastName"):
                self.authors.append(creator["lastName"])
        if not self.authors:
            self.authors = ["Unknown"]
        self.is_research_format = True

    def generate_windows_filename(self) -> str:
        """Assembles and truncates the filename using ONLY internal state properties."""
        title_clean: str = self._sanitize(self.title)
        source_clean: str = self._sanitize(self.source)

        if title_clean.upper() in self.reserved_names:
            title_clean += "_"

        author_str: str = "+".join(self.authors)
        if len(self.authors) > 1:
            test_name: str = f"{self.prefix} {title_clean} - {author_str}, {source_clean}{self.ext}"
            if len(test_name) > self.limit:
                author_str = self.authors[0] + " et al."

        name: str = f"{self.prefix} {title_clean} - {author_str}, {source_clean}{self.ext}"
        if len(name) <= self.limit:
            return name

        fixed_len: int = len(self.prefix) + 1 + 3 + len(author_str) + 2 + len(source_clean) + len(self.ext)
        max_title_len: int = self.limit - fixed_len - 3

        if max_title_len > 0:
            return f"{self.prefix} {title_clean[:max_title_len]}... - {author_str}, {source_clean}{self.ext}"

        return name[:self.limit]

    def get_canonical_key(self) -> str:
        """Generates a pure alphanumeric matching lookup key directly from the generated name state."""
        filename: str = self.generate_windows_filename()
        clean: str = re.sub(r"[^a-zA-Z0-9]", "", filename).lower()
        return clean[:50]

    # region: PRIVATE INTERNAL CLOCKWORK METHODS
    def _sanitize(self, text: str) -> str:
        if not text:
            return ""
        txt: str = re.sub(r"<[^>]+>", "", text)
        txt = "".join(char for char in txt if ord(char) >= 32)

        translation_map: Dict[str, str] = {
            "\u201c": "'", "\u201d": "'", "\u2018": "'", "\u2019": "'", "\"": "'",
            "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " ",
            ":": ","
        }
        for orig, rep in translation_map.items():
            txt = txt.replace(orig, rep)

        return re.sub(r"[<>:\"/\\|?*]", "_", txt)

    def _calculate_date_from_prefix(self, year_str: str, q_str: str, ref_str: str) -> Optional[str]:
        """Reverse Calculation: Parses YYYY, Q, and D## into an ISO YYYY-MM-DD string."""
        try:
            year: int = int(year_str)
            quarter: int = int(q_str)
            day_offset: int = int(ref_str[1:]) - 1  # Strip the 'D' and drop to a 0-indexed delta

            # Locate the calendar boundaries of the target quarter
            start_month: int = (quarter - 1) * 3 + 1
            q_start_date: datetime = datetime(year, start_month, 1)

            actual_date: datetime = q_start_date + timedelta(days=day_offset)
            return actual_date.strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            return None

    def _calculate_prefix_from_date(self, pub_date_str: Optional[str], page_ref: Optional[str]) -> str:
        """Forward Calculation: Converts an ISO date into a structured chronological prefix."""
        year: str; quarter: str; ref: str
        year, quarter, ref = "0000", "0", "000"
        if pub_date_str:
            try:
                dt: datetime = datetime.strptime(pub_date_str[:10], "%Y-%m-%d")
                year: str = str(dt.year)
                q_num: int = (dt.month - 1) // 3 + 1
                quarter: str = str(q_num)

                q_start_date: datetime = datetime(dt.year, (q_num - 1) * 3 + 1, 1)
                ref: str = "D" + str((dt - q_start_date).days + 1)
                return f"{year}.{quarter}.{ref}"
            except ValueError:
                if len(pub_date_str) >= 4: year = pub_date_str[:4]
                if page_ref: ref = page_ref
        elif page_ref:
            ref = page_ref
        return f"{year}.{quarter}.{ref}"
    # endregion

class FreeplaneMap:
    """Encapsulates the generation of a Freeplane .mm XML file."""

    @staticmethod
    def get_best_link(zotero_uri: Optional[str], onedrive_url: Optional[str]) -> Optional[str]:
        """
        Link Prioritizer: Resolves the optimal URI for the mind map node.
        Priority 1: Zotero local database URI
        Priority 2: OneDrive web URL
        """
        if zotero_uri:
            return zotero_uri
        return onedrive_url

    class MapNode:
        """Represents a single node (folder or file) within the mind map."""
        def __init__(self, text: str, link: Optional[str] = None, depth: int = 0):
            self.text: str = text
            self.link: Optional[str] = link
            self.depth: int = depth
            self.children: List['FreeplaneMap.MapNode'] = []

        def add_child(self, text: str, link: Optional[str] = None) -> 'FreeplaneMap.MapNode':
            """
            Instantiates a child node, automatically incrementing its depth attribute 
            based on its parent's location in the tree.
            """
            # Pass the inherited depth + 1 to the new child
            child: 'FreeplaneMap.MapNode' = FreeplaneMap.MapNode(text, link, depth=self.depth + 1)
            self.children.append(child)
            return child

        def render(self) -> str:
            """Recursively generates the XML string for this node, injecting style attributes."""
            safe_text: str = self.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")

            node_xml: str = f'<node TEXT="{safe_text}"'
            if self.link:
                safe_link: str = self.link.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
                node_xml += f' LINK="{safe_link}"'

            node_xml += ">"

            # Inject the standard Freeplane Attribute for the Conditional Styles engine
            node_xml += f'<attribute NAME="Depth" VALUE="{self.depth}"/>'

            for child in self.children:
                node_xml += child.render()

            node_xml += "</node>"
            return node_xml

    def __init__(self, root_text: str):
        # The core root node acts as the 0-depth anchor
        self.root_node: 'FreeplaneMap.MapNode' = self.MapNode(root_text, depth=0)

    def save(self, filepath: str) -> None:
        """Compiles the complete XML document and writes it to disk."""
        xml_header: str = '<?xml version="1.0" encoding="UTF-8"?>\n<map version="1.9.13">\n'
        xml_footer: str = '\n</map>'

        full_xml: str = f"{xml_header}{self.root_node.render()}{xml_footer}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_xml)
        print(f"Freeplane map successfully generated at: {filepath}")

class TransferSession:
    # region: INITIALIZATION & CONFIGURATION
    def __init__(self):
        # Get configuration strings natively from environment space
        self.google_creds_path: str = os.environ.get("GOOGLE_CREDS_PATH", "")
        self.ms_client_id: str = os.environ.get("MS_CLIENT_ID", "")
        self.ms_client_secret: str = os.environ.get("MS_CLIENT_SECRET", "")
        self.ms_authority: str = os.environ.get("MS_AUTHORITY", "")
        self.root_folder_id: str = os.environ.get("ROOT_FOLDER_ID", "")

        self.zotero_user_id: str = os.environ.get("ZOTERO_USER_ID", "")
        self.zotero_api_key: str = os.environ.get("ZOTERO_API_KEY", "")

        # Verify critical parameters are loaded
        if not all([
                self.google_creds_path,
                self.ms_client_id,
                self.ms_client_secret,
                self.zotero_user_id,
                self.zotero_api_key
            ]):
            raise EnvironmentError("Missing required environment configuration tokens inside .env file.")

        # 1. Initialize Zotero Session with Global Headers
        self.ms_token: Optional[str] = None
        self.zotero_session: requests.Session = requests.Session()
        self.zotero_session.headers.update({
            "Zotero-API-Key": self.zotero_api_key,
            "Content-Type": "application/json"
        })

        # 2. Configure Retries and Timeouts for resilience
        # This handles transient network "burps" without manual intervention
        retry_strategy: Retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST", "PUT"]
        )
        adapter: HTTPAdapter = HTTPAdapter(max_retries=retry_strategy)
        self.zotero_session.mount("https://", adapter)

        # 3. Instantiate the single long-lived naming engine context
        self.naming_context: ResearchFileContext = ResearchFileContext()

        # 4. Initialize the Freeplane Map with a root node
        self.map_engine: FreeplaneMap = FreeplaneMap("My Life and Worldview")

        # 5. Mappings
        self.checkpoint: Dict[str, Any] = self._load_json(CHECKPOINT_FILE)
        self.zotero_map: Dict[str, str] = self._load_json(COLLECTION_MAP_FILE)

        # Maps binary MD5 checksums -> OneDrive resource webUrls for global deduplication
        self.content_map: Dict[str, str] = self._load_json(CONTENT_MAP_FILE)

        # In-memory namespace verification pool to avoid OneDrive name collisions
        self.used_names: Set[str] = self._initialize_used_names()

    def _load_json(self, filepath: str) -> Dict[str, Any]:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _initialize_used_names(self) -> Set[str]:
        """Populates the runtime namespace safety set from active checkpoint history."""
        names: Set[str] = set()
        for entry in self.checkpoint.values():
            if "name" in entry:
                names.add(entry["name"])
        return names

    def calculate_stream_hash(self, data_bytes: bytes) -> str:
        """
        Computes a deterministic MD5 hexadecimal checksum for an in-memory 
        binary file stream to handle global content-addressable deduplication.
        """
        hasher = hashlib.md5()
        hasher.update(data_bytes)
        return hasher.hexdigest()

    def _atomic_json_write(self, filepath: str, data: Dict[str, Any]) -> None:
        """
        Writes JSON data to a temporary file first, then renames it.
        This ensures the original file is never corrupted if the write fails.
        """
        dir_name = os.path.dirname(os.path.abspath(filepath))
        # Create a temporary file in the same directory as the target file
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2, ensure_ascii=False)
            temp_name = tf.name

        # Atomically replace the old file with the new one
        # On Windows, this may require os.replace() or handling existing file
        try:
            os.replace(temp_name, filepath)
        except Exception as e:
            if os.path.exists(temp_name):
                os.remove(temp_name)
            print(f"Failed to save state to {filepath}: {str(e)}")
            raise
    # endregion

    # region: AUTHENTICATION & API INTERACTION
    def save_state(self) -> None:
        """
        Atomically saves both the checkpoint and Zotero folder mappings.
        """
        # Save the Checkpoint (The "Where am I?" data)
        self._atomic_json_write(CHECKPOINT_FILE, self.checkpoint)

        # Save the Zotero Map (The "What is the structure?" data)
        self._atomic_json_write(COLLECTION_MAP_FILE, self.zotero_map)

        # Save the Content Map (The "What is the content?" data)
        self._atomic_json_write(CONTENT_MAP_FILE, self.content_map)

        print("State successfully persisted to disk.")

    def authenticate_all(self) -> None:
        """Initializes both Google and Microsoft authentication."""
        self._auth_google()
        self._auth_microsoft()
    # endregion

    # region: MICROSOFT GRAPH HELPERS
    def _auth_microsoft(self) -> None:
        scopes = ["Files.ReadWrite.All", "User.Read"]
        app = msal.PublicClientApplication(self.ms_client_id, authority=self.ms_authority)

        accounts: List[Dict[str, Any]] = app.get_accounts()  # type: ignore
        result: Dict[str, Any] = {}  # type: ignore

        if accounts:
            result: Dict[str, Any] = app.acquire_token_silent(scopes, account=accounts[0])  # type: ignore

        if not result:
            # Note: This triggers the browser flow
            result: Dict[str, Any] = app.acquire_token_interactive(scopes=scopes)  # type: ignore[return-value]

        if "access_token" in result:
            self.ms_token = result["access_token"]
        else:
            raise RuntimeError("Could not authenticate Microsoft account.")

    def resolve_windows_namespace(self, filename: str) -> str:
        """
        Coordinates global namespace uniqueness. Calculates the necessary alpha suffix
        and updates the shared ResearchFileContext state if a collision occurs.
        """
        # Load the file into the context machine once to initialize its properties
        self.naming_context.load_file_context(filename)

        # Generate the standard un-suffixed filename
        candidate_name = self.naming_context.generate_windows_filename()

        # If the standard name is unique, register it and exit immediately
        if candidate_name not in self.used_names:
            self.used_names.add(candidate_name)
            return candidate_name

        # A collision occurred. Loop until a clean alpha suffix slot is uncovered
        ascii_pointer = 65  # ASCII for 'A'

        while candidate_name in self.used_names:
            if ascii_pointer > 90:  # Past "Z" -> Handle overflow (AA, AB, etc.)
                cycle_count = (ascii_pointer - 65) // 26
                remainder_offset = (ascii_pointer - 65) % 26
                suffix = chr(65 + cycle_count - 1) + chr(65 + remainder_offset)
            else:
                suffix = chr(ascii_pointer)

            # Feed the calculated suffix directly to the context state
            self.naming_context.alpha_suffix = suffix

            # Re-evaluate the filename string calculated by the context engine
            candidate_name = self.naming_context.generate_windows_filename()
            ascii_pointer += 1

        self.used_names.add(candidate_name)
        print(f"Prefix Collision Resolved: '{filename}' -> '{candidate_name}'")
        return candidate_name

    def _upload_onedrive_file(self, filename: str, data: bytes) -> str:
        """
        Uploads an in-memory byte stream to OneDrive.
        Uses a standard PUT for files <4MB and a chunked resumable session for larger files.
        """
        size = len(data)
        encoded_name = quote(filename)
        base_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/Documents/My%20Life%20and%20Worldview/{encoded_name}"
        auth_headers = {"Authorization": f"Bearer {self.ms_token}"}

        # Small File Upload (<= 4MB)
        if size <= 4 * 1024 * 1024:
            url = f"{base_url}:/content"
            headers = {**auth_headers, "Content-Type": "application/octet-stream"}
            response = requests.put(url, headers=headers, data=data)
            response.raise_for_status()
            return response.json().get("webUrl", "")

        # Large File Resumable Upload Session
        print(f"Large file detected ({size / 1024 / 1024:.2f} MB). Starting chunked session...")
        session_url = f"{base_url}:/createUploadSession"
        session_response = requests.post(session_url, headers=auth_headers)
        session_response.raise_for_status()

        upload_url = session_response.json()["uploadUrl"]
        chunk_size = 327680 * 10  # ~3.2MB per chunk (must be multiple of 320 KiB)
        last_response: Optional[requests.Response] = None
        data_view = memoryview(data)
        max_retries = 5

        for start in range(0, size, chunk_size):
            end = min(start + chunk_size - 1, size - 1)
            chunk_data = data_view[start:end + 1]

            headers = {
                "Content-Length": str(len(chunk_data)),
                "Content-Range": f"bytes {start}-{end}/{size}"
            }

            for attempt in range(max_retries):
                try:
                    # Authorization header is deliberately excluded from the PUT to the upload_url
                    last_response = requests.put(upload_url, headers=headers, data=chunk_data)
                    if last_response.status_code in (200, 201, 202):
                        break
                    elif last_response.status_code >= 500:
                        print(f"Server error {last_response.status_code}. Retrying chunk...")
                    else:
                        raise RuntimeError(f"Chunk upload failed at {start}-{end}: {last_response.text}")
                except Exception as e:
                    print(f"Connection error ({type(e).__name__}). Retrying chunk...")

                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise RuntimeError(f"Failed to upload chunk {start}-{end} after {max_retries} attempts.")

        return last_response.json().get("webUrl", "") if last_response else ""
    # endregion

    # region: ZOTERO HELPERS
    def zotero_request(self, method: str, endpoint: str, **kwargs: Any) -> requests.Response:
        """
        Executes a Zotero API call with a mandatory timeout and rate-limit delay.
        """
        # Ensure we always have a reasonable timeout (e.g., 30 seconds)
        if "timeout" not in kwargs:
            kwargs["timeout"] = 30

        url = f"https://api.zotero.org/users/{self.zotero_user_id}/{endpoint}"

        try:
            response = self.zotero_session.request(method, url, **kwargs)
            response.raise_for_status()

            # MANDATORY RATE LIMIT: 
            # Zotero allows ~50 requests per min; 0.5s-1s delay is a safe conservative buffer.
            time.sleep(0.7) 

            return response
        except requests.exceptions.RequestException as e:
            print(f"Zotero API Error at {endpoint}: {str(e)}")
            raise

    def build_zotero_index(self) -> Dict[str, str]:
        """
        Scans the Zotero web library on startup to build a local memory index.
        Maps the compiled Canonical Key to the persistent Zotero Item Key.
        """
        print("Indexing Zotero library for title matching...")
        index: Dict[str, str] = {}
        limit: int = 100
        start: int = 0

        while True:
            endpoint: str = f"items?itemType=document&limit={limit}&start={start}"
            resp: List[Dict[str, Any]] = self.zotero_request("GET", endpoint).json()

            if not resp:
                break

            for item in resp:
                # Ingest raw database values directly into our stateful context
                self.naming_context.load_zotero_context(item["data"])

                # Extract the derived canonical key straight from class state
                norm_key: str = self.naming_context.get_canonical_key()
                index[norm_key] = item["key"]

            start += limit

        print(f"Indexed {len(index)} Zotero document records successfully.")
        return index

    def get_or_create_zotero_collection(self, name: str, parent_id: Optional[str] = None) -> str:
        """
        Resolves a folder name to a Zotero Collection ID. 
        Creates the collection if it does not exist and persists the mapping.
        """
        path_key = f"{parent_id or 'ROOT'}/{name}"

        if path_key in self.zotero_map:
            return self.zotero_map[path_key]

        payload: Dict[str, Any] = {
            "name": name, 
            "parentCollection": parent_id
        }

        response: requests.Response = self.zotero_request("POST", "collections", json=[payload])
        new_id: str = response.json()["successful"]["0"]["key"]

        self.zotero_map[path_key] = new_id
        self.save_state()  # Persist structural changes immediately
        return new_id

    def get_or_create_research_item(self, g_filename: str, onedrive_url: str, collection_id: str, z_index: Dict[str, str]) -> str:
        """
        Coordinates the verification, population, and synchronization of Zotero records
        leveraging the internal naming state.
        """
        # 1. Load the incoming file from Google Drive into our state machine
        self.naming_context.load_file_context(g_filename)

        # Extract the lookup token to see if this document already exists in Zotero
        lookup_key: str = self.naming_context.get_canonical_key()
        item_key: Optional[str] = z_index.get(lookup_key)

        if item_key:
            # Pull the raw item metadata schema from the web api
            item_data: Dict[str, Any] = self.zotero_request("GET", f"items/{quote(item_key)}").json()["data"]

            # Re-assign the collections block to place it into the new mirrored structure
            item_data["collections"] = [collection_id]

            # If our filename contained a valid chronological D-day prefix, update the database
            if self.naming_context.full_date:
                item_data["date"] = self.naming_context.full_date

            self.zotero_request("PUT", f"items/{quote(item_key)}", json=item_data)
            print(f"Moved existing Zotero item: {self.naming_context.title}")

            # Flush out historical attachment pointers to prevent stale Google Drive links
            children = self.zotero_request("GET", f"items/{quote(item_key)}/children").json()
            for child in children:
                if child["data"].get("itemType") == "attachment":
                    self.zotero_request("DELETE", f"items/{quote(child['key'])}")
        else:
            # Construct the schema properties strictly using internal context state
            creators: List[Dict[str, str]] = []
            for author in self.naming_context.authors:
                creators.append({"creatorType": "author", "lastName": author})

            # Build clean metadata fields utilizing calculated chronological fallbacks
            new_item_payload: Dict[str, Any] = {
                "itemType": "document",
                "title": self.naming_context.title,
                "creators": creators,
                "collections": [collection_id],
                "date": self.naming_context.full_date or self.naming_context.prefix.split(".")[0],
                "publisher": self.naming_context.source
            }

            # Map page numbers directly to Zotero's formal 'pages' field
            if self.naming_context.page_number:
                new_item_payload["pages"] = self.naming_context.page_number

            # Store the entire prefix string as a legacy reference anchor
            if self.naming_context.is_research_format:
                new_item_payload["extra"] = f"Prefix: {self.naming_context.prefix}"

            create_resp: requests.Response = self.zotero_request("POST", "items", json=[new_item_payload])
            item_key: Optional[str] = create_resp.json()["successful"]["0"]["key"]
            print(f"Created new Zotero item: {self.naming_context.title}")

        # 2. ATTACH RE-MAPPED ONEDRIVE POINTER
        attachment_payload: Dict[str, Any] = {
            "itemType": "attachment",
            "linkMode": "linked_url",
            "parentItem": item_key,
            "title": "View in OneDrive",
            "url": onedrive_url,
            "collections": [collection_id]
        }
        self.zotero_request("POST", "items", json=[attachment_payload])

        # Return the definitive select link for your Freeplane map node
        return f"zotero://select/library/items/{item_key}"
    # endregion

    # region: FILE/FOLDER PROCESSING LOGIC

    # endregion
