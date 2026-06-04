import os
import json
import msal # type: ignore
import xml.etree.ElementTree as ET
import re
import time
import hashlib
import requests
import tempfile

from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow # type: ignore
from google.auth.transport.requests import Request
from googleapiclient.discovery import build, Resource # type: ignore
from requests.adapters import HTTPAdapter
from typing import Any, Dict, List, Optional, Set
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

class TransferSession:
    def __init__(self):
        # Fetch configuration strings natively from environment space
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
        self.g_service: Optional[Resource] = None
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
        self.naming_context = ResearchFileContext()

        # 4. Mappings
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

    def _auth_google(self) -> None:
        scopes: List[str] = ["https://www.googleapis.com/auth/drive.readonly"]
        creds: Optional[Credentials] = None # type: ignore
        token_path: str = "token_google.json"

        if os.path.exists(token_path):
            creds: Optional[Credentials] = Credentials.from_authorized_user_file(token_path, scopes)  # type: ignore

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:  # type: ignore
                creds.refresh(Request())  # type: ignore
            else:
                flow: InstalledAppFlow = InstalledAppFlow.from_client_secrets_file(self.google_creds_path, scopes)  # type: ignore
                creds: Credentials = flow.run_local_server(port=0)  # type: ignore

            with open(token_path, "w") as token:
                token.write(creds.to_json())  # type: ignore

        self.g_service = build("drive", "v3", credentials=creds)

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
    def __init__(self, root_text: str = "My Worldview", version: str = "1.9.13"):
        """Initializes the XML structure with a central root node."""
        self.root_xml: ET.Element = ET.Element("map", version=version)
        self.central_node: ET.Element = ET.SubElement(self.root_xml, "node", TEXT=root_text)

    def add_styled_node(self, parent_node: ET.Element, name: str, link: Optional[str], depth: int) -> ET.Element:
        """
        Adds a new child node to the mindmap.
        Implements the attribute-based styling for the semantic zoom engine.
        """
        node_attrs: Dict[str, str] = {"TEXT": name}
        if link and link != "NONE":
            node_attrs["LINK"] = link

        node: ET.Element = ET.SubElement(parent_node, "node", node_attrs)

        # Always inject the 'Depth' attribute for conditional styling in Freeplane
        ET.SubElement(node, "attribute", NAME="Depth", VALUE=str(depth))
        return node

    def get_best_link(self, zotero_uri: Optional[str], onedrive_url: Optional[str]) -> Optional[str]:
        """
        Helper that prioritizes Zotero URIs over OneDrive URLs.
        """
        if zotero_uri and zotero_uri != "NONE":
            return zotero_uri
        if onedrive_url and onedrive_url != "NONE":
            return onedrive_url
        return None

    def write(self, filepath: str) -> None:
        """Serializes the XML tree to disk."""
        tree = ET.ElementTree(self.root_xml)
        tree.write(filepath, encoding="utf-8", xml_declaration=True)
        print(f"Mindmap successfully written to {filepath}")
