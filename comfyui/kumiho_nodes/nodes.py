"""
Kumiho ComfyUI Custom Nodes

This module contains the custom node definitions for integrating
Kumiho Cloud asset management with ComfyUI workflows.

Primary Nodes:
- KumihoSaveImage: Save images to disk and register with Kumiho Cloud
- KumihoSaveVideo: Save videos to disk and register with Kumiho Cloud  
- KumihoLoadAsset: Load assets from Kumiho Cloud using kref URIs
"""

import os
import json
import hashlib
import tempfile
import threading
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path

import torch
import numpy as np
from PIL import Image

# ComfyUI imports
try:
    import folder_paths
    HAS_COMFYUI = True
except ImportError:
    HAS_COMFYUI = False
    folder_paths = None

try:
    from server import PromptServer
    from aiohttp import web
    HAS_PROMPT_SERVER = True
except ImportError:
    HAS_PROMPT_SERVER = False
    web = None

# Kumiho SDK imports (optional)
# Enable auto-configure on import
os.environ.setdefault("KUMIHO_AUTO_CONFIGURE", "1")

try:
    import kumiho
    from kumiho import DEPENDS_ON, DERIVED_FROM, REFERENCED, CONTAINS, CREATED_FROM
    HAS_KUMIHO_SDK = True
    print(f"[Kumiho] SDK loaded successfully (version: {getattr(kumiho, '__version__', 'unknown')})")
    
    # Log tenant info if available
    try:
        _tenant_info = kumiho.get_tenant_info()
        if _tenant_info:
            print(f"[Kumiho] Tenant: {_tenant_info.get('tenant_name')} (id: {_tenant_info.get('tenant_id', 'unknown')[:8]}...)")
    except Exception:
        pass
except ImportError as e:
    HAS_KUMIHO_SDK = False
    kumiho = None
    DEPENDS_ON = DERIVED_FROM = REFERENCED = CONTAINS = CREATED_FROM = None
    print(f"[Kumiho] SDK not available: {e}")
    print("[Kumiho] Install with: pip install kumiho && kumiho-cli login")
except Exception as e:
    HAS_KUMIHO_SDK = False
    kumiho = None
    DEPENDS_ON = DERIVED_FROM = REFERENCED = CONTAINS = CREATED_FROM = None
    print(f"[Kumiho] SDK failed to initialize: {e}")


# =============================================================================
# Configuration Constants
# =============================================================================

DEFAULT_PROJECT_TEMPLATE = "comfyui-{tenant}"
DEFAULT_API_ENDPOINT = "https://api.kumiho.cloud"
CACHE_TTL_SECONDS = 300  # 5 minute cache for asset lists

# =============================================================================
# Asset Type Definitions
# =============================================================================

# Map ComfyUI folder types to Kumiho space paths and kinds
ASSET_TYPE_MAP = {
    "checkpoints": {
        "space": "checkpoint",
        "kind": "checkpoint",
        "extensions": [".safetensors", ".ckpt", ".pt"],
        "subfolders": ["sd15", "sdxl", "flux", "sd3"]
    },
    "loras": {
        "space": "lora",
        "kind": "lora",
        "extensions": [".safetensors"],
        "subfolders": ["sd15", "sdxl", "flux"]
    },
    "vae": {
        "space": "vae",
        "kind": "vae",
        "extensions": [".safetensors", ".pt"],
        "subfolders": []
    },
    "controlnet": {
        "space": "controlnet",
        "kind": "controlnet",
        "extensions": [".safetensors", ".pth"],
        "subfolders": ["sd15", "sdxl"]
    },
    "upscale_models": {
        "space": "upscale/image",
        "kind": "upscaler",
        "extensions": [".pth", ".pt"],
        "subfolders": []
    },
    "clip_vision": {
        "space": "clip/vision",
        "kind": "clip_vision",
        "extensions": [".safetensors"],
        "subfolders": []
    },
    "embeddings": {
        "space": "embedding",
        "kind": "embedding",
        "extensions": [".safetensors", ".pt"],
        "subfolders": []
    },
    "gligen": {
        "space": "gligen",
        "kind": "gligen",
        "extensions": [".safetensors"],
        "subfolders": []
    },
    "style_models": {
        "space": "style",
        "kind": "style_model",
        "extensions": [".safetensors"],
        "subfolders": []
    },
    "ipadapter": {
        "space": "ipadapter",
        "kind": "ipadapter",
        "extensions": [".safetensors", ".bin"],
        "subfolders": []
    },
    "animatediff_models": {
        "space": "animatediff/models",
        "kind": "animatediff_model",
        "extensions": [".safetensors", ".ckpt"],
        "subfolders": []
    },
    "animatediff_motion_lora": {
        "space": "animatediff/motion_lora",
        "kind": "motion_lora",
        "extensions": [".safetensors", ".ckpt"],
        "subfolders": []
    },
    "diffusion_models": {
        "space": "diffusion",
        "kind": "diffusion_model",
        "extensions": [".safetensors"],
        "subfolders": []
    },
    "text_encoders": {
        "space": "text/encoders",
        "kind": "text_encoder",
        "extensions": [".safetensors"],
        "subfolders": []
    },
    "clip": {
        "space": "clip",
        "kind": "clip",
        "extensions": [".safetensors"],
        "subfolders": []
    },
    # Input assets - kind is determined dynamically based on file extension
    "input": {
        "space": "input",
        "kind": None,  # Determined dynamically by get_input_kind()
        "extensions": [],  # Accepts any file type
        "subfolders": []
    },
}

# File extensions to kind mapping for input assets
INPUT_KIND_MAP = {
    # Images
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".bmp": "image",
    ".tiff": "image",
    ".tif": "image",
    ".exr": "image",
    ".hdr": "image",
    # Videos
    ".mp4": "video",
    ".webm": "video",
    ".mov": "video",
    ".avi": "video",
    ".mkv": "video",
    # Audio
    ".mp3": "audio",
    ".wav": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    # 3D/Mesh
    ".obj": "mesh",
    ".fbx": "mesh",
    ".gltf": "mesh",
    ".glb": "mesh",
    ".stl": "mesh",
    ".ply": "mesh",
    # Text/Data
    ".txt": "text",
    ".json": "data",
    ".yaml": "data",
    ".yml": "data",
    ".csv": "data",
    # Masks (typically grayscale images)
    ".mask": "mask",
}


def get_input_kind(file_path: str) -> str:
    """Determine the kind for an input asset based on file extension.
    
    Args:
        file_path: Path to the input file
        
    Returns:
        Kind string (image, video, audio, mesh, text, data, or 'asset' as fallback)
    """
    if not file_path:
        return "asset"
    
    ext = Path(file_path).suffix.lower()
    return INPUT_KIND_MAP.get(ext, "asset")


# Map node class names to the asset types they load
NODE_ASSET_MAP = {
    # Checkpoint loaders
    "CheckpointLoaderSimple": ("checkpoints", "ckpt_name"),
    "CheckpointLoader": ("checkpoints", "ckpt_name"),
    "unCLIPCheckpointLoader": ("checkpoints", "ckpt_name"),
    "UNETLoader": ("diffusion_models", "unet_name"),
    
    # LoRA loaders
    "LoraLoader": ("loras", "lora_name"),
    "LoraLoaderModelOnly": ("loras", "lora_name"),
    
    # Model loaders
    "ControlNetLoader": ("controlnet", "control_net_name"),
    "VAELoader": ("vae", "vae_name"),
    "UpscaleModelLoader": ("upscale_models", "model_name"),
    "CLIPVisionLoader": ("clip_vision", "clip_name"),
    "CLIPLoader": ("clip", "clip_name"),
    "DualCLIPLoader": ("clip", "clip_name1"),  # Has clip_name1 and clip_name2
    "GLIGENLoader": ("gligen", "gligen_name"),
    "StyleModelLoader": ("style_models", "style_model_name"),
    
    # Image loaders
    "LoadImage": ("input", "image"),
    "LoadImageMask": ("input", "image"),
}

# Edge types for lineage
EDGE_TYPES = {
    "CREATED_FROM": "Output was created from this workflow",
    "USED_MODEL": "Used this checkpoint/diffusion model",
    "USED_LORA": "Used this LoRA model",
    "USED_CONTROLNET": "Used this ControlNet model",
    "USED_VAE": "Used this VAE model",
    "USED_INPUT": "Used this input image/asset",
    "USED_EMBEDDING": "Used this text embedding",
    "DERIVED_FROM": "Derived from (img2img)",
}


# =============================================================================
# Asset Catalog (for Dropdown Population)
# =============================================================================

class KumihoAssetCatalog:
    """
    Manages cached asset lists from Kumiho Cloud for dropdown population.
    
    This catalog refreshes in the background and provides:
    - Projects list
    - Spaces list (as categories)
    - Items list for each space
    - Search functionality
    
    Uses BYO Storage model - files are local, only metadata is in cloud.
    """
    
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._cache_timestamp: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._refresh_thread: Optional[threading.Thread] = None
        self._loaded = False
        self._projects: List[str] = []
        self._spaces: Dict[str, List[str]] = {}  # project -> spaces
        self._items: Dict[str, List[Dict]] = {}  # "project/space" -> items
    
    def _cache_key(self, *args) -> str:
        return "/".join(str(a) for a in args)
    
    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache_timestamp:
            return False
        return (time.time() - self._cache_timestamp[key]) < CACHE_TTL_SECONDS
    
    def refresh(self, background: bool = True):
        """Refresh the catalog from Kumiho Cloud."""
        if background:
            if self._refresh_thread and self._refresh_thread.is_alive():
                return  # Already refreshing
            self._refresh_thread = threading.Thread(target=self._do_refresh, daemon=True)
            self._refresh_thread.start()
        else:
            self._do_refresh()
    
    def _do_refresh(self):
        """Actual refresh logic."""
        if not HAS_KUMIHO_SDK:
            print("[Kumiho] SDK not available, using fallback catalog")
            self._load_fallback()
            return
        
        try:
            # Use kumiho SDK to fetch catalog (auto-configured on import)
            # Get projects
            projects = kumiho.get_projects()
            with self._lock:
                self._projects = [p.name for p in projects] if projects else []
                self._cache_timestamp['projects'] = time.time()
            
            # For each project, get spaces
            for project_name in self._projects[:5]:  # Limit to 5 projects
                try:
                    project = kumiho.get_project(project_name)
                    if project:
                        spaces = project.get_spaces(recursive=True)
                        space_names = [s.path for s in spaces] if spaces else []
                        with self._lock:
                            self._spaces[project_name] = space_names
                            self._cache_timestamp[f'spaces/{project_name}'] = time.time()
                except Exception as e:
                    print(f"[Kumiho] Error getting spaces for {project_name}: {e}")
            
            self._loaded = True
            print("[Kumiho] Catalog refreshed successfully")
            
        except Exception as e:
            print(f"[Kumiho] Error refreshing catalog: {e}")
            self._load_fallback()
    
    def _load_fallback(self):
        """Load fallback data when SDK is not available."""
        with self._lock:
            self._projects = ["ComfyUI@Default"]
            self._spaces["ComfyUI@Default"] = [
                "checkpoint/sd15", "checkpoint/sdxl", "checkpoint/flux",
                "lora/sd15", "lora/sdxl", "lora/flux",
                "vae", "controlnet", "embedding", "upscale"
            ]
            self._loaded = True
    
    def get_projects(self) -> List[str]:
        """Get list of projects."""
        if not self._loaded:
            self.refresh(background=False)
        with self._lock:
            return self._projects.copy() if self._projects else ["ComfyUI@Default"]
    
    def get_spaces(self, project: str = None) -> List[str]:
        """Get list of spaces, optionally filtered by project."""
        if not self._loaded:
            self.refresh(background=False)
        with self._lock:
            if project and project in self._spaces:
                return self._spaces[project].copy()
            # Return all spaces
            all_spaces = []
            for p, spaces in self._spaces.items():
                all_spaces.extend([f"{p}/{s}" for s in spaces])
            return all_spaces if all_spaces else ["checkpoint", "lora", "vae"]
    
    def get_items_for_space(self, space_path: str) -> List[Tuple[str, str]]:
        """
        Get items in a space as (display_name, kref) tuples.
        
        Args:
            space_path: Full space path like "ComfyUI@Default/checkpoint/flux"
        
        Returns:
            List of (display_name, kref_uri) tuples
        """
        cache_key = self._cache_key('items', space_path)
        
        with self._lock:
            if cache_key in self._items and self._is_cache_valid(cache_key):
                return [(i['name'], i['kref']) for i in self._items[cache_key]]
        
        items = []
        if HAS_KUMIHO_SDK:
            try:
                # Parse space path
                parts = space_path.split('/', 1)
                if len(parts) == 2:
                    project, space = parts
                    result = kumiho.item_search(context_filter=space_path)
                    if result:
                        items = [
                            {'name': item.name, 'kref': f"kref://{space_path}/{item.name}.{item.kind}"}
                            for item in result
                        ]
            except Exception as e:
                print(f"[Kumiho] Error getting items for {space_path}: {e}")
        
        with self._lock:
            self._items[cache_key] = items
            self._cache_timestamp[cache_key] = time.time()
        
        return [(i['name'], i['kref']) for i in items]
    
    def search(self, query: str, kind_filter: str = "") -> List[Dict]:
        """Search for items across all projects."""
        if not HAS_KUMIHO_SDK:
            return []
        
        try:
            results = kumiho.item_search(
                name_filter=f"*{query}*",
                kind_filter=kind_filter
            )
            return [
                {
                    'name': item.name,
                    'kind': item.kind,
                    'kref': f"kref://{item.project}/{item.space}/{item.name}.{item.kind}",
                    'path': f"{item.project}/{item.space}"
                }
                for item in (results or [])
            ]
        except Exception as e:
            print(f"[Kumiho] Search error: {e}")
            return []


# Global catalog instance
_asset_catalog: Optional[KumihoAssetCatalog] = None
_catalog_lock = threading.Lock()


def get_asset_catalog() -> KumihoAssetCatalog:
    """Get or create the global asset catalog."""
    global _asset_catalog
    with _catalog_lock:
        if _asset_catalog is None:
            _asset_catalog = KumihoAssetCatalog()
            _asset_catalog.refresh(background=True)
        return _asset_catalog


def refresh_catalog():
    """Trigger a catalog refresh."""
    catalog = get_asset_catalog()
    catalog.refresh(background=True)


# =============================================================================
# API Routes for Dynamic Dropdown Population
# =============================================================================

if HAS_PROMPT_SERVER:
    @PromptServer.instance.routes.get("/kumiho/catalog/projects")
    async def get_projects_api(request):
        """API endpoint to get available projects."""
        catalog = get_asset_catalog()
        projects = catalog.get_projects()
        return web.json_response({
            "projects": projects,
            "status": "ok"
        })
    
    @PromptServer.instance.routes.get("/kumiho/catalog/spaces")
    async def get_spaces_api(request):
        """API endpoint to get available spaces."""
        project = request.query.get("project", None)
        catalog = get_asset_catalog()
        spaces = catalog.get_spaces(project)
        return web.json_response({
            "spaces": spaces,
            "project": project,
            "status": "ok"
        })
    
    @PromptServer.instance.routes.get("/kumiho/catalog/items")
    async def get_items_api(request):
        """API endpoint to get items in a space."""
        space_path = request.query.get("space", "")
        catalog = get_asset_catalog()
        items = catalog.get_items_for_space(space_path)
        return web.json_response({
            "items": [{"name": name, "kref": kref} for name, kref in items],
            "space": space_path,
            "status": "ok"
        })
    
    @PromptServer.instance.routes.get("/kumiho/catalog/search")
    async def search_items_api(request):
        """API endpoint to search for items."""
        query = request.query.get("q", "")
        kind = request.query.get("kind", "")
        catalog = get_asset_catalog()
        results = catalog.search(query, kind_filter=kind)
        return web.json_response({
            "results": results,
            "query": query,
            "kind": kind,
            "status": "ok"
        })
    
    @PromptServer.instance.routes.post("/kumiho/catalog/refresh")
    async def refresh_catalog_api(request):
        """API endpoint to trigger catalog refresh."""
        refresh_catalog()
        return web.json_response({
            "status": "ok",
            "message": "Catalog refresh triggered"
        })

# =============================================================================
# Utility Functions
# =============================================================================

def sanitize_tenant_for_project(tenant_name: str) -> str:
    """Sanitize tenant name for use in project names.
    
    Converts spaces and special characters to valid project name format.
    """
    import re
    # Replace spaces and special chars with hyphens
    sanitized = re.sub(r"[^a-zA-Z0-9-]", "-", tenant_name)
    # Remove consecutive hyphens
    sanitized = re.sub(r"-+", "-", sanitized)
    # Remove leading/trailing hyphens and convert to lowercase
    return sanitized.strip("-").lower() or "default"


def get_tenant_name() -> str:
    """Get the tenant name/slug from discovery cache, environment, or fallback to default.
    
    Priority order:
    1. Kumiho SDK get_tenant_slug() - returns URL-safe slug or shortened tenant_id
    2. KUMIHO_TENANT environment variable
    3. 'default' as fallback
    """
    # Try to get from SDK discovery cache first (already sanitized/validated)
    if HAS_KUMIHO_SDK:
        try:
            slug = kumiho.get_tenant_slug()
            if slug:
                return slug
        except Exception:
            pass
    
    # Fall back to environment variable (needs sanitization)
    env_tenant = os.environ.get("KUMIHO_TENANT")
    if env_tenant:
        return sanitize_tenant_for_project(env_tenant)
    
    return "default"


def get_default_project() -> str:
    """Get the default ComfyUI project name using tenant slug."""
    tenant = get_tenant_name()
    return DEFAULT_PROJECT_TEMPLATE.format(tenant=tenant)


def get_configured_project() -> str:
    """Return the project to use, relying on SDK/tenant info (no disk persistence)."""
    # Highest priority: explicit env override
    env_project = os.environ.get("KUMIHO_PROJECT")
    if env_project and env_project.strip():
        return env_project.strip()

    # Prefer SDK-provided tenant slug if available
    if HAS_KUMIHO_SDK:
        try:
            slug = kumiho.get_tenant_slug()
            if slug:
                return DEFAULT_PROJECT_TEMPLATE.format(tenant=slug)
        except Exception:
            pass

    # Fallback to derived default
    return get_default_project()


def parse_kref(kref: str) -> Dict[str, Any]:
    """
    Parse a kref:// URI into its components.
    
    Format: kref://project/space/subspace/item.kind?r=revision&a=artifact_type
    
    Examples:
    - kref://ComfyUI@KumihoClouds/lora/flux/Eye-Lora.lora?r=1&a=lora
    - kref://ComfyUI@KumihoClouds/checkpoint/flux/flux1-schnell.checkpoint?r=latest&a=fp8
    """
    if not kref.startswith('kref://'):
        raise ValueError(f"Invalid kref URI: {kref}")
    
    # Remove scheme
    path = kref[7:]
    
    # Parse query parameters
    query_params = {}
    if '?' in path:
        path, query = path.split('?', 1)
        for param in query.split('&'):
            if '=' in param:
                key, value = param.split('=', 1)
                query_params[key] = value
    
    # Split path
    parts = path.split('/')
    if len(parts) < 3:
        raise ValueError(f"Invalid kref path format: {kref}")
    
    project = parts[0]
    
    # Last part is item.kind
    item_with_kind = parts[-1]
    if '.' not in item_with_kind:
        raise ValueError(f"Item must include kind: {kref}")
    
    item_name, kind = item_with_kind.rsplit('.', 1)
    
    # Middle parts are the space path
    space_path = '/'.join(parts[1:-1])
    
    return {
        'project': project,
        'space': space_path,
        'item': item_name,
        'kind': kind,
        'revision': query_params.get('r', 'latest'),
        'artifact_type': query_params.get('a'),
        'full_kref': kref
    }


def build_kref(project: str, space: str, item: str, kind: str,
               revision: Optional[str] = None, artifact_type: Optional[str] = None) -> str:
    """Build a kref:// URI from components."""
    kref = f"kref://{project}/{space}/{item}.{kind}"
    
    params = []
    if revision:
        params.append(f"r={revision}")
    if artifact_type:
        params.append(f"a={artifact_type}")
    
    if params:
        kref += '?' + '&'.join(params)
    
    return kref


def detect_base_model(file_path: str) -> str:
    """
    Detect the base model type from the file path.
    Returns: sd15, sdxl, flux, sd3, or unknown
    """
    path_lower = file_path.lower()
    
    if 'flux' in path_lower:
        return 'flux'
    elif 'sdxl' in path_lower or 'xl' in path_lower:
        return 'sdxl'
    elif 'sd3' in path_lower:
        return 'sd3'
    elif 'sd15' in path_lower or 'sd1.5' in path_lower or 'v1-5' in path_lower:
        return 'sd15'
    else:
        return 'unknown'


def get_file_hash(file_path: str, algorithm: str = 'sha256') -> str:
    """Calculate file hash for deduplication."""
    hasher = hashlib.new(algorithm)
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def tensor_to_pil(tensor: torch.Tensor) -> Image.Image:
    """Convert ComfyUI image tensor [B,H,W,C] to PIL Image."""
    if tensor.dim() == 4:
        tensor = tensor[0]
    np_image = tensor.cpu().numpy()
    np_image = np.clip(np_image * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(np_image)


def pil_to_tensor(image: Image.Image) -> torch.Tensor:
    """Convert PIL Image to ComfyUI image tensor [1,H,W,C]."""
    if image.mode != 'RGB':
        image = image.convert('RGB')
    np_image = np.array(image).astype(np.float32) / 255.0
    return torch.from_numpy(np_image).unsqueeze(0)


# =============================================================================
# Workflow Parser
# =============================================================================

class WorkflowParser:
    """Parse ComfyUI workflow JSON to extract dependencies."""
    
    def __init__(self, workflow: Dict[str, Any]):
        self.workflow = workflow
        self.dependencies = []
        self.nodes = {}
    
    def parse(self) -> Dict[str, Any]:
        """Parse the workflow and extract all dependencies."""
        self._parse_nodes()
        self._extract_dependencies()
        
        return {
            'nodes': self.nodes,
            'dependencies': self.dependencies,
            'workflow_json': json.dumps(self.workflow)
        }
    
    def extract_seeds(self) -> Dict[str, Any]:
        """Extract seed values from workflow nodes.
        
        Returns a dict mapping node_id -> seed_value for nodes that have seeds.
        Common seed-bearing nodes: KSampler, KSamplerAdvanced, SamplerCustom, etc.
        """
        seeds = {}
        seed_node_types = {
            'KSampler': 'seed',
            'KSamplerAdvanced': 'noise_seed',
            'SamplerCustom': 'noise_seed',
            'SamplerCustomAdvanced': 'noise_seed',
            'RandomNoise': 'noise_seed',
            'KSamplerSelect': 'seed',
        }
        
        for node_id, node_info in self.nodes.items():
            node_type = node_info.get('type', '')
            
            if node_type in seed_node_types:
                seed_key = seed_node_types[node_type]
                inputs = node_info.get('inputs', {})
                
                # Try to get seed from inputs dict (API format)
                if isinstance(inputs, dict) and seed_key in inputs:
                    seed_val = inputs.get(seed_key)
                    if seed_val is not None and not isinstance(seed_val, list):  # Not a connection
                        seeds[node_id] = {
                            'node_type': node_type,
                            'seed_key': seed_key,
                            'value': seed_val
                        }
                # Also check widgets_values (workflow format)
                elif 'widgets_values' in node_info:
                    widgets = node_info.get('widgets_values', [])
                    # Seed is typically one of the first widget values
                    for i, val in enumerate(widgets):
                        if isinstance(val, (int, float)) and val > 1000:  # Seeds are usually large numbers
                            seeds[node_id] = {
                                'node_type': node_type,
                                'seed_key': seed_key,
                                'widget_index': i,
                                'value': val
                            }
                            break
        
        return seeds
    
    def extract_generation_settings(self) -> Dict[str, Any]:
        """Extract generation settings from workflow nodes.
        
        Extracts prompt, negative prompt, model, LoRAs, steps, CFG, sampler, etc.
        These are stored as revision metadata for the asset browser to display.
        
        Returns:
            Dict with keys matching asset-browser's ItemMetadata:
            - prompt: positive prompt text
            - negative_prompt: negative prompt text  
            - model: checkpoint/model name
            - loras: comma-separated LoRA names
            - seed: seed value (first found)
            - steps: number of steps
            - cfg: CFG scale
            - sampler: sampler name
            - scheduler: scheduler name
            - width: image width
            - height: image height
        """
        settings = {}
        
        # Extract from KSampler nodes (steps, cfg, sampler, scheduler, seed)
        sampler_node_types = {
            'KSampler': {'steps': 'steps', 'cfg': 'cfg', 'sampler': 'sampler_name', 'scheduler': 'scheduler', 'seed': 'seed'},
            'KSamplerAdvanced': {'steps': 'steps', 'cfg': 'cfg', 'sampler': 'sampler_name', 'scheduler': 'scheduler', 'seed': 'noise_seed'},
            'SamplerCustom': {'steps': 'steps', 'cfg': 'cfg', 'seed': 'noise_seed'},
            'SamplerCustomAdvanced': {'seed': 'noise_seed'},
        }
        
        # Extract from CLIPTextEncode nodes (prompts)
        prompts_found = []
        negative_prompts_found = []
        
        # Extract model and LoRA names
        models_found = []
        loras_found = []
        
        # Extract image dimensions from EmptyLatentImage or similar
        width = None
        height = None
        
        for node_id, node_info in self.nodes.items():
            node_type = node_info.get('type', '')
            inputs = node_info.get('inputs', {})
            widgets = node_info.get('widgets_values', [])
            
            # Extract sampler settings
            if node_type in sampler_node_types:
                param_map = sampler_node_types[node_type]
                for setting_key, input_key in param_map.items():
                    if isinstance(inputs, dict) and input_key in inputs:
                        val = inputs[input_key]
                        if val is not None and not isinstance(val, list):  # Not a connection
                            if setting_key not in settings:  # Keep first found
                                settings[setting_key] = val
            
            # Extract prompts from CLIPTextEncode
            if node_type == 'CLIPTextEncode':
                text = None
                if isinstance(inputs, dict) and 'text' in inputs:
                    text = inputs.get('text')
                elif widgets:
                    text = widgets[0] if widgets else None
                
                if text and isinstance(text, str):
                    # Try to determine if positive or negative based on common patterns
                    text_lower = text.lower()
                    is_negative = any(neg in text_lower for neg in [
                        'ugly', 'bad', 'worst', 'blurry', 'nsfw', 'watermark',
                        'deformed', 'disfigured', 'mutated', 'lowres', 'low quality'
                    ])
                    if is_negative:
                        negative_prompts_found.append(text)
                    else:
                        prompts_found.append(text)
            
            # Extract model name from CheckpointLoaderSimple
            if node_type in ['CheckpointLoaderSimple', 'CheckpointLoader', 'UNETLoader']:
                ckpt_name = None
                if isinstance(inputs, dict) and 'ckpt_name' in inputs:
                    ckpt_name = inputs.get('ckpt_name')
                elif isinstance(inputs, dict) and 'unet_name' in inputs:
                    ckpt_name = inputs.get('unet_name')
                elif widgets:
                    ckpt_name = widgets[0] if widgets else None
                
                if ckpt_name and isinstance(ckpt_name, str):
                    # Clean up the name (remove path, extension)
                    model_name = Path(ckpt_name).stem
                    models_found.append(model_name)
            
            # Extract LoRA names
            if node_type in ['LoraLoader', 'LoraLoaderModelOnly']:
                lora_name = None
                if isinstance(inputs, dict) and 'lora_name' in inputs:
                    lora_name = inputs.get('lora_name')
                elif widgets:
                    lora_name = widgets[0] if widgets else None
                
                if lora_name and isinstance(lora_name, str):
                    lora_clean = Path(lora_name).stem
                    # Also extract strength if available
                    strength = None
                    if isinstance(inputs, dict):
                        strength = inputs.get('strength_model') or inputs.get('strength')
                    elif len(widgets) > 1:
                        strength = widgets[1] if isinstance(widgets[1], (int, float)) else None
                    
                    if strength is not None:
                        loras_found.append(f"{lora_clean}:{strength}")
                    else:
                        loras_found.append(lora_clean)
            
            # Extract dimensions from EmptyLatentImage
            if node_type in ['EmptyLatentImage', 'EmptySD3LatentImage']:
                if isinstance(inputs, dict):
                    if 'width' in inputs and not isinstance(inputs['width'], list):
                        width = inputs['width']
                    if 'height' in inputs and not isinstance(inputs['height'], list):
                        height = inputs['height']
                elif widgets and len(widgets) >= 2:
                    width = widgets[0] if isinstance(widgets[0], int) else None
                    height = widgets[1] if isinstance(widgets[1], int) else None
        
        # Compile results
        if prompts_found:
            settings['prompt'] = prompts_found[0]  # Use first positive prompt
        if negative_prompts_found:
            settings['negative_prompt'] = negative_prompts_found[0]
        if models_found:
            settings['model'] = models_found[0]  # Use first model
        if loras_found:
            settings['loras'] = ','.join(loras_found)
        if width and height:
            settings['width'] = str(width)
            settings['height'] = str(height)
        
        # Convert numeric values to strings for metadata storage
        for key in ['seed', 'steps', 'cfg']:
            if key in settings:
                settings[key] = str(settings[key])
        
        return settings
    
    def _parse_nodes(self):
        """Parse all nodes in the workflow."""
        # Handle both prompt format and workflow format
        nodes_dict = self.workflow
        
        if 'nodes' in self.workflow:
            # Full workflow format
            for node in self.workflow.get('nodes', []):
                node_id = str(node.get('id'))
                self.nodes[node_id] = {
                    'type': node.get('type'),
                    'widgets_values': node.get('widgets_values', []),
                    'inputs': node.get('inputs', []),
                }
        else:
            # Prompt format (from API)
            for node_id, node_data in self.workflow.items():
                if isinstance(node_data, dict) and 'class_type' in node_data:
                    self.nodes[node_id] = {
                        'type': node_data.get('class_type'),
                        'inputs': node_data.get('inputs', {}),
                    }
    
    def _extract_dependencies(self):
        """Extract all file dependencies from nodes."""
        for node_id, node_info in self.nodes.items():
            node_type = node_info.get('type', '')
            
            if node_type in NODE_ASSET_MAP:
                asset_type, input_name = NODE_ASSET_MAP[node_type]
                
                # Get the input value
                inputs = node_info.get('inputs', {})
                if isinstance(inputs, dict):
                    value = inputs.get(input_name)
                elif isinstance(inputs, list):
                    # widgets_values format
                    widgets = node_info.get('widgets_values', [])
                    value = widgets[0] if widgets else None
                else:
                    value = None
                
                if value and isinstance(value, str):
                    self.dependencies.append({
                        'node_id': node_id,
                        'node_type': node_type,
                        'asset_type': asset_type,
                        'input_name': input_name,
                        'value': value,
                        'file_path': self._resolve_path(asset_type, value)
                    })
    
    def _resolve_path(self, asset_type: str, value: str) -> Optional[str]:
        """Resolve a value to a full file path."""
        if not HAS_COMFYUI or folder_paths is None:
            return value
        
        try:
            if asset_type == 'checkpoints':
                return folder_paths.get_full_path('checkpoints', value)
            elif asset_type == 'loras':
                return folder_paths.get_full_path('loras', value)
            elif asset_type == 'vae':
                return folder_paths.get_full_path('vae', value)
            elif asset_type == 'controlnet':
                return folder_paths.get_full_path('controlnet', value)
            elif asset_type == 'upscale_models':
                return folder_paths.get_full_path('upscale_models', value)
            elif asset_type == 'clip_vision':
                return folder_paths.get_full_path('clip_vision', value)
            elif asset_type == 'input':
                return folder_paths.get_annotated_filepath(value)
            else:
                return value
        except Exception:
            return value


# =============================================================================
# KumihoSave Base Class (shared logic)
# =============================================================================

class _KumihoSaveBase:
    """Base class with shared save and registration logic for image and video nodes."""
    
    OUTPUT_NODE = True
    
    def _is_revision_invalid_error(self, error: Exception) -> bool:
        """Return True when the SDK reports a missing or published revision."""
        message = str(error)
        return "Revision not found or is published" in message or "PERMISSION_DENIED" in message
    
    def _parse_workflow(self, prompt, extra_pnginfo):
        """Parse workflow to extract dependencies, seeds, and generation settings."""
        workflow_data = {}
        dependencies = []
        seeds = {}
        generation_settings = {}
        
        if prompt:
            parser = WorkflowParser(prompt)
            parsed = parser.parse()
            workflow_data = parsed
            dependencies = parsed.get('dependencies', [])
            seeds = parser.extract_seeds()
            generation_settings = parser.extract_generation_settings()
        
        return workflow_data, dependencies, seeds, generation_settings
    
    def _build_lineage(self, project, category, artifact_name, kind, 
                       media_info, dependencies, seeds, generation_settings,
                       auto_register_deps, create_lineage,
                       prompt, extra_pnginfo):
        """Build lineage data structure."""
        lineage = {
            'output': {
                'project': project,
                'space': category,
                'artifact_name': artifact_name,
                'kind': kind,
                **media_info,
            },
            'dependencies': [],
            'edges': [],
            'seeds': seeds,  # Store extracted seed values
            'generation_settings': generation_settings,  # Store generation settings for asset browser
            'timestamp': datetime.now().isoformat(),
        }
        
        # Process dependencies
        if auto_register_deps and dependencies:
            for dep in dependencies:
                asset_type = dep.get('asset_type')
                file_path = dep.get('file_path') or dep.get('value')
                node_type = dep.get('node_type')
                
                if not file_path:
                    continue
                
                # Determine the Kumiho asset info
                asset_info = ASSET_TYPE_MAP.get(asset_type, {})
                space = asset_info.get('space', asset_type)
                
                # For input assets, determine kind dynamically based on file extension
                if asset_type == 'input':
                    dep_kind = get_input_kind(file_path)
                else:
                    dep_kind = asset_info.get('kind', asset_type)
                
                # Detect base model for subfoldering
                base_model = detect_base_model(file_path)
                if base_model != 'unknown' and asset_type in ['checkpoints', 'loras', 'controlnet']:
                    space = f"{space}/{base_model}"
                
                # Generate item name from file path
                file_name = Path(file_path).stem if file_path else dep.get('value', 'unknown')
                
                dep_kref = build_kref(project, space, file_name, dep_kind)
                
                dep_info = {
                    'node_id': dep.get('node_id'),
                    'node_type': node_type,
                    'asset_type': asset_type,
                    'file_path': file_path,
                    'kref': dep_kref,
                    'status': 'pending'
                }
                
                lineage['dependencies'].append(dep_info)
                
                # Determine edge type
                edge_type = 'USED_INPUT'
                if asset_type == 'checkpoints' or asset_type == 'diffusion_models':
                    edge_type = 'USED_MODEL'
                elif asset_type == 'loras':
                    edge_type = 'USED_LORA'
                elif asset_type == 'controlnet':
                    edge_type = 'USED_CONTROLNET'
                elif asset_type == 'vae':
                    edge_type = 'USED_VAE'
                elif asset_type == 'embeddings':
                    edge_type = 'USED_EMBEDDING'
                
                if create_lineage:
                    lineage['edges'].append({
                        'edge_type': edge_type,
                        'target_kref': dep_kref,
                        'metadata': {
                            'node_id': dep.get('node_id'),
                            'node_type': node_type,
                        }
                    })
        
        # Store workflow JSON as metadata
        lineage['workflow'] = {
            'prompt': prompt,
            'extra_pnginfo': extra_pnginfo,
        }
        
        return lineage
    
    def _register_with_kumiho(self, project, category, artifact_name, kind,
                               saved_paths, lineage, description, tags, create_lineage, timestamp):
        """Register with Kumiho Cloud and create lineage edges."""
        output_kref = build_kref(project, category, artifact_name, kind, revision='new')
        
        if not HAS_KUMIHO_SDK:
            print("[Kumiho] SDK not available - skipping cloud registration")
            print("[Kumiho] Install with: pip install kumiho && kumiho-cli login")
            return output_kref
        
        print(f"[Kumiho] Starting registration for {artifact_name} in {project}/{category}")
        
        try:
            # Get or create the project
            print(f"[Kumiho] Getting project: {project}")
            project_obj = kumiho.get_project(project)
            if not project_obj:
                print(f"[Kumiho] Project not found, creating: {project}")
                project_obj = kumiho.create_project(project, f"ComfyUI outputs for {project}")
            
            # Validate project was created/retrieved successfully
            if not project_obj:
                print(f"[Kumiho] ERROR: Failed to get or create project: {project}")
                return output_kref
            
            project_name = project_obj.name
            print(f"[Kumiho] Using project: {project_name} (id: {getattr(project_obj, 'project_id', 'unknown')})")
            
            # Navigate/create space hierarchy
            # Always use the full path from project root for space creation
            space_parts = category.split('/')
            current_space = None
            
            for i, part in enumerate(space_parts):
                space_path = '/'.join(space_parts[:i+1])
                print(f"[Kumiho] Getting space: {space_path}")
                
                # get_space throws NOT_FOUND exception if space doesn't exist
                try:
                    current_space = project_obj.get_space(space_path)
                except Exception as space_err:
                    # Space doesn't exist, need to create it
                    print(f"[Kumiho] Space not found ({space_err.__class__.__name__}), will create: {part}")
                    current_space = None
                
                if not current_space:
                    # For root-level space (i=0), parent_path should be None 
                    # which makes SDK use /{project_name} as parent
                    # For nested spaces, use full path: /{project_name}/{parent_space_path}
                    if i > 0:
                        parent_path = f"/{project_name}/{'/'.join(space_parts[:i])}"
                    else:
                        parent_path = None  # SDK will use /{project_name}
                    
                    print(f"[Kumiho] Creating space: {part} (parent: {parent_path or f'/{project_name}'})")
                    current_space = project_obj.create_space(part, parent_path=parent_path)
            
            if current_space:
                print(f"[Kumiho] Using space: {current_space.path if hasattr(current_space, 'path') else current_space}")
                
                # Get or create item (item_name stays the same, revisions are auto-versioned)
                print(f"[Kumiho] Getting/creating item: {artifact_name} (kind: {kind})")
                try:
                    item = current_space.get_item(artifact_name, kind)
                    print(f"[Kumiho] Found existing item: {artifact_name}")
                except Exception:
                    # Item doesn't exist, create it
                    print(f"[Kumiho] Item not found, creating: {artifact_name}")
                    item = current_space.create_item(artifact_name, kind)
                    item.set_metadata(metadata={
                        "source": "comfyui",
                        **{k: str(v) for k, v in lineage['output'].items() 
                           if k not in ['project', 'space', 'artifact_name', 'kind', 'saved_paths', 'output_directory', 'kref']}
                    })
                
                # Create revision with full workflow for guaranteed reproducibility
                # Storing full workflow per revision ensures any workflow changes are captured
                print(f"[Kumiho] Creating revision...")
                
                # Serialize workflow JSON
                workflow_json = None
                if lineage.get('workflow'):
                    try:
                        workflow_json = json.dumps(lineage['workflow'], default=str)
                    except Exception as json_err:
                        print(f"[Kumiho] Warning: Could not serialize workflow: {json_err}")
                
                # Also store seeds for quick reference (without parsing full workflow)
                seeds = lineage.get('seeds', {})
                seeds_json = None
                if seeds:
                    try:
                        seeds_json = json.dumps(seeds, default=str)
                    except Exception:
                        pass
                
                # Get generation settings for asset browser display
                gen_settings = lineage.get('generation_settings', {})
                
                # Build revision metadata - include generation settings as top-level keys
                # so asset browser can easily read prompt, model, steps, cfg, etc.
                revision_metadata = {
                    "timestamp": lineage['timestamp'],
                    "description": description,
                    "tags": tags,
                    # "workflow": workflow_json,  # Removed to prevent metadata bloat and recursion
                    "seeds": seeds_json,  # Seeds for quick reference
                    # Generation settings for asset browser (top-level for easy access)
                    **gen_settings,
                }
                
                revision = item.create_revision(metadata=revision_metadata)
                print(f"[Kumiho] Revision created with generation settings: {list(gen_settings.keys())}")
                
                # Create artifacts for each saved file
                artifact_entries = []
                for idx, path in enumerate(saved_paths):
                    artifact_suffix = f"_{idx}" if len(saved_paths) > 1 else ""
                    artifact_name_full = f"{kind}{artifact_suffix}"
                    artifact_entries.append((artifact_name_full, path))
                
                created_artifacts = []
                retried_revision = False
                for artifact_name_full, path in artifact_entries:
                    print(f"[Kumiho] Creating artifact: {artifact_name_full} -> {path}")
                    try:
                        revision.create_artifact(artifact_name_full, path)
                        created_artifacts.append((artifact_name_full, path))
                    except Exception as create_err:
                        if (not retried_revision) and self._is_revision_invalid_error(create_err):
                            print("[Kumiho] Revision invalid, recreating revision and retrying artifacts")
                            revision = item.create_revision(metadata=revision_metadata)
                            retried_revision = True
                            retry_entries = created_artifacts + [(artifact_name_full, path)]
                            created_artifacts = []
                            for retry_name, retry_path in retry_entries:
                                revision.create_artifact(retry_name, retry_path)
                                created_artifacts.append((retry_name, retry_path))
                        else:
                            raise
                
                # Tag if requested
                if tags:
                    for tag in tags.split(','):
                        tag = tag.strip()
                        if tag:
                            print(f"[Kumiho] Applying tag: {tag}")
                            revision.tag(tag)
                
                # Create lineage edges from dependencies
                if create_lineage and lineage.get('dependencies'):
                    print(f"[Kumiho] Processing {len(lineage['dependencies'])} dependencies for lineage...")
                    
                    # Map custom edge types to SDK edge types
                    edge_type_map = {
                        'USED_MODEL': DEPENDS_ON,
                        'USED_LORA': DEPENDS_ON,
                        'USED_VAE': DEPENDS_ON,
                        'USED_CONTROLNET': DEPENDS_ON,
                        'USED_EMBEDDING': DEPENDS_ON,
                        'USED_INPUT': REFERENCED,
                        'DEPENDS_ON': DEPENDS_ON,
                        'DERIVED_FROM': DERIVED_FROM,
                        'REFERENCED': REFERENCED,
                    }
                    
                    for dep_info in lineage['dependencies']:
                        try:
                            file_path = dep_info.get('file_path')
                            asset_type = dep_info.get('asset_type')
                            node_type = dep_info.get('node_type')
                            
                            if not file_path:
                                print(f"[Kumiho] Skipping dependency without file_path: {dep_info}")
                                continue
                            
                            print(f"[Kumiho] Processing dependency: {file_path}")
                            
                            # Check if this file is already registered with Kumiho
                            dep_revision = None
                            existing_artifacts = kumiho.get_artifacts_by_location(file_path)
                            
                            if existing_artifacts:
                                # File is already registered, use its revision
                                artifact = existing_artifacts[0]  # Use first match
                                dep_revision = artifact.get_revision()
                                dep_kref = ""
                                if dep_revision and hasattr(dep_revision, "kref"):
                                    dep_kref = dep_revision.kref
                                elif hasattr(artifact, "kref"):
                                    dep_kref = artifact.kref
                                if dep_kref and not dep_kref.startswith(f"kref://{project_name}/"):
                                    print(f"[Kumiho] Found artifact in different project, re-registering in current project: {dep_kref}")
                                    dep_revision = None
                                else:
                                    print(f"[Kumiho] Found existing artifact: {artifact.kref} -> revision {dep_revision.number if dep_revision else 'unknown'}")
                            
                            if not dep_revision:
                                # File not registered in this project, register it now
                                print(f"[Kumiho] File not registered in current project, registering: {file_path}")
                                
                                # Get asset info from mapping
                                asset_info = ASSET_TYPE_MAP.get(asset_type, {})
                                dep_space = asset_info.get('space', asset_type)
                                
                                # For input assets, determine kind dynamically based on file extension
                                if asset_type == 'input':
                                    dep_kind = get_input_kind(file_path)
                                else:
                                    dep_kind = asset_info.get('kind', asset_type)
                                
                                # Detect base model for subfoldering
                                base_model = detect_base_model(file_path)
                                if base_model != 'unknown' and asset_type in ['checkpoints', 'loras', 'controlnet']:
                                    dep_space = f"{dep_space}/{base_model}"
                                
                                # Generate item name from file path
                                file_name = Path(file_path).stem
                                
                                # Create space hierarchy for dependency
                                dep_space_parts = dep_space.split('/')
                                dep_current_space = None
                                
                                for i, part in enumerate(dep_space_parts):
                                    dep_space_path = '/'.join(dep_space_parts[:i+1])
                                    try:
                                        dep_current_space = project_obj.get_space(dep_space_path)
                                    except Exception:
                                        dep_current_space = None
                                    
                                    if not dep_current_space:
                                        if i > 0:
                                            parent_path = f"/{project_name}/{'/'.join(dep_space_parts[:i])}"
                                        else:
                                            parent_path = None
                                        print(f"[Kumiho] Creating dep space: {part}")
                                        dep_current_space = project_obj.create_space(part, parent_path=parent_path)
                                
                                if dep_current_space:
                                    # Get or create the dependency item
                                    try:
                                        dep_item = dep_current_space.get_item(file_name, dep_kind)
                                        print(f"[Kumiho] Found existing dep item: {file_name}")
                                    except Exception:
                                        print(f"[Kumiho] Creating dep item: {file_name}.{dep_kind}")
                                        dep_item = dep_current_space.create_item(file_name, dep_kind)
                                        dep_item.set_metadata(metadata={
                                            "source": "comfyui-auto",
                                            "asset_type": asset_type,
                                            "base_model": base_model,
                                        })
                                    
                                    # Create revision for the dependency
                                    dep_revision = dep_item.create_revision(metadata={
                                        "registered_from": "comfyui-lineage",
                                        "original_path": file_path,
                                    })
                                    
                                    # Create artifact pointing to the file
                                    dep_revision.create_artifact(dep_kind, file_path)
                                    print(f"[Kumiho] Registered dep: {dep_revision.kref}")
                            
                            # Create edge if we have a dependency revision
                            if dep_revision:
                                # Determine edge type based on asset type
                                edge_type_str = 'USED_INPUT'
                                if asset_type in ['checkpoints', 'diffusion_models']:
                                    edge_type_str = 'USED_MODEL'
                                elif asset_type == 'loras':
                                    edge_type_str = 'USED_LORA'
                                elif asset_type == 'controlnet':
                                    edge_type_str = 'USED_CONTROLNET'
                                elif asset_type == 'vae':
                                    edge_type_str = 'USED_VAE'
                                elif asset_type == 'embeddings':
                                    edge_type_str = 'USED_EMBEDDING'
                                
                                edge_type = edge_type_map.get(edge_type_str, DEPENDS_ON)
                                
                                revision.create_edge(
                                    dep_revision,
                                    edge_type,
                                    metadata={
                                        'node_type': node_type,
                                        'asset_type': asset_type,
                                        'file_path': file_path,
                                    }
                                )
                                print(f"[Kumiho] Created edge: {revision.kref} --[{edge_type_str}]--> {dep_revision.kref}")
                                dep_info['status'] = 'linked'
                            else:
                                print(f"[Kumiho] Warning: Could not get/create revision for {file_path}")
                                dep_info['status'] = 'failed'
                                
                        except Exception as edge_err:
                            print(f"[Kumiho] Warning: Could not process dependency: {edge_err}")
                            dep_info['status'] = 'error'
                
                # Update output_kref with actual revision
                output_kref = revision.kref
                print(f"[Kumiho] ✓ Successfully registered: {output_kref}")
            else:
                print(f"[Kumiho] ERROR: Could not create/get space for category: {category}")
                
        except Exception as e:
            import traceback
            print(f"[Kumiho] ERROR: Could not register with Kumiho Cloud: {e}")
            print(f"[Kumiho] Traceback: {traceback.format_exc()}")
        
        return output_kref
    
    def _register_video_with_kumiho(self, project, category, artifact_name, kind,
                                     saved_paths, preview_gif_path, lineage, description, tags, create_lineage, timestamp):
        """Register video with Kumiho Cloud, including preview GIF as additional artifact."""
        output_kref = build_kref(project, category, artifact_name, kind, revision='new')
        
        if not HAS_KUMIHO_SDK:
            print("[Kumiho] SDK not available - skipping cloud registration")
            print("[Kumiho] Install with: pip install kumiho && kumiho-cli login")
            return output_kref
        
        print(f"[Kumiho] Starting video registration for {artifact_name} in {project}/{category}")
        
        try:
            # Get or create the project
            project_obj = kumiho.get_project(project)
            if not project_obj:
                print(f"[Kumiho] Project not found, creating: {project}")
                project_obj = kumiho.create_project(project, f"ComfyUI outputs for {project}")
            
            if not project_obj:
                print(f"[Kumiho] ERROR: Failed to get or create project: {project}")
                return output_kref
            
            project_name = project_obj.name
            
            # Navigate/create space hierarchy
            space_parts = category.split('/')
            current_space = None
            
            for i, part in enumerate(space_parts):
                space_path = '/'.join(space_parts[:i+1])
                try:
                    current_space = project_obj.get_space(space_path)
                except Exception:
                    current_space = None
                
                if not current_space:
                    if i > 0:
                        parent_path = f"/{project_name}/{'/'.join(space_parts[:i])}"
                    else:
                        parent_path = None
                    current_space = project_obj.create_space(part, parent_path=parent_path)
            
            if current_space:
                # Get or create item
                try:
                    item = current_space.get_item(artifact_name, kind)
                except Exception:
                    item = current_space.create_item(artifact_name, kind)
                    item.set_metadata(metadata={
                        "source": "comfyui",
                        **{k: str(v) for k, v in lineage['output'].items() 
                           if k not in ['project', 'space', 'artifact_name', 'kind', 'saved_paths', 'output_directory', 'kref', 'preview_path']}
                    })
                
                # Create revision
                workflow_json = None
                if lineage.get('workflow'):
                    try:
                        workflow_json = json.dumps(lineage['workflow'], default=str)
                    except Exception:
                        pass
                
                seeds_json = None
                if lineage.get('seeds'):
                    try:
                        seeds_json = json.dumps(lineage['seeds'], default=str)
                    except Exception:
                        pass
                
                revision_metadata = {
                    "timestamp": lineage['timestamp'],
                    "description": description,
                    "tags": tags,
                    "workflow": workflow_json,
                    "seeds": seeds_json,
                }
                revision = item.create_revision(metadata=revision_metadata)
                
                # Create artifacts for video files and preview
                artifact_entries = []
                for idx, path in enumerate(saved_paths):
                    artifact_suffix = f"_{idx}" if len(saved_paths) > 1 else ""
                    artifact_name_full = f"{kind}{artifact_suffix}"
                    artifact_entries.append((artifact_name_full, path))
                
                if preview_gif_path and os.path.exists(preview_gif_path):
                    artifact_entries.append(("preview", preview_gif_path))
                
                created_artifacts = []
                retried_revision = False
                for artifact_name_full, path in artifact_entries:
                    print(f"[Kumiho] Creating artifact: {artifact_name_full} -> {path}")
                    try:
                        revision.create_artifact(artifact_name_full, path)
                        created_artifacts.append((artifact_name_full, path))
                    except Exception as create_err:
                        if (not retried_revision) and self._is_revision_invalid_error(create_err):
                            print("[Kumiho] Revision invalid, recreating revision and retrying artifacts")
                            revision = item.create_revision(metadata=revision_metadata)
                            retried_revision = True
                            retry_entries = created_artifacts + [(artifact_name_full, path)]
                            created_artifacts = []
                            for retry_name, retry_path in retry_entries:
                                revision.create_artifact(retry_name, retry_path)
                                created_artifacts.append((retry_name, retry_path))
                        else:
                            raise
                
                # Tag if requested
                if tags:
                    for tag in tags.split(','):
                        tag = tag.strip()
                        if tag:
                            revision.tag(tag)
                
                # Create lineage edges (same as base method)
                if create_lineage and lineage.get('dependencies'):
                    edge_type_map = {
                        'USED_MODEL': DEPENDS_ON,
                        'USED_LORA': DEPENDS_ON,
                        'USED_VAE': DEPENDS_ON,
                        'USED_CONTROLNET': DEPENDS_ON,
                        'USED_EMBEDDING': DEPENDS_ON,
                        'USED_INPUT': REFERENCED,
                        'DEPENDS_ON': DEPENDS_ON,
                        'DERIVED_FROM': DERIVED_FROM,
                        'REFERENCED': REFERENCED,
                    }
                    
                    for dep_info in lineage['dependencies']:
                        try:
                            file_path = dep_info.get('file_path')
                            asset_type = dep_info.get('asset_type')
                            node_type = dep_info.get('node_type')
                            
                            if not file_path:
                                continue
                            
                            dep_revision = None
                            existing_artifacts = kumiho.get_artifacts_by_location(file_path)
                            
                            if existing_artifacts:
                                artifact = existing_artifacts[0]
                                dep_revision = artifact.get_revision()
                                dep_kref = ""
                                if dep_revision and hasattr(dep_revision, "kref"):
                                    dep_kref = dep_revision.kref
                                elif hasattr(artifact, "kref"):
                                    dep_kref = artifact.kref
                                if dep_kref and not dep_kref.startswith(f"kref://{project_name}/"):
                                    dep_revision = None
                            
                            if not dep_revision:
                                # File not registered in this project, register it now
                                asset_info = ASSET_TYPE_MAP.get(asset_type, {})
                                dep_space = asset_info.get('space', asset_type)
                                
                                # For input assets, determine kind dynamically based on file extension
                                if asset_type == 'input':
                                    dep_kind = get_input_kind(file_path)
                                else:
                                    dep_kind = asset_info.get('kind', asset_type)
                                
                                # Detect base model for subfoldering
                                base_model = detect_base_model(file_path)
                                if base_model != 'unknown' and asset_type in ['checkpoints', 'loras', 'controlnet']:
                                    dep_space = f"{dep_space}/{base_model}"
                                
                                # Generate item name from file path
                                file_name = Path(file_path).stem
                                
                                # Create space hierarchy for dependency
                                dep_space_parts = dep_space.split('/')
                                dep_current_space = None
                                
                                for i, part in enumerate(dep_space_parts):
                                    dep_space_path = '/'.join(dep_space_parts[:i+1])
                                    try:
                                        dep_current_space = project_obj.get_space(dep_space_path)
                                    except Exception:
                                        dep_current_space = None
                                    
                                    if not dep_current_space:
                                        if i > 0:
                                            parent_path = f"/{project_name}/{'/'.join(dep_space_parts[:i])}"
                                        else:
                                            parent_path = None
                                        dep_current_space = project_obj.create_space(part, parent_path=parent_path)
                                
                                if dep_current_space:
                                    # Get or create the dependency item
                                    try:
                                        dep_item = dep_current_space.get_item(file_name, dep_kind)
                                    except Exception:
                                        dep_item = dep_current_space.create_item(file_name, dep_kind)
                                        dep_item.set_metadata(metadata={
                                            "source": "comfyui-auto",
                                            "asset_type": asset_type,
                                            "base_model": base_model,
                                        })
                                    
                                    # Create revision for the dependency
                                    dep_revision = dep_item.create_revision(metadata={
                                        "registered_from": "comfyui-lineage",
                                        "original_path": file_path,
                                    })
                                    
                                    # Create artifact pointing to the file
                                    dep_revision.create_artifact(dep_kind, file_path)
                            
                            if dep_revision:
                                edge_type_str = 'USED_INPUT'
                                if asset_type in ['checkpoints', 'diffusion_models']:
                                    edge_type_str = 'USED_MODEL'
                                elif asset_type == 'loras':
                                    edge_type_str = 'USED_LORA'
                                elif asset_type == 'controlnet':
                                    edge_type_str = 'USED_CONTROLNET'
                                elif asset_type == 'vae':
                                    edge_type_str = 'USED_VAE'
                                elif asset_type == 'embeddings':
                                    edge_type_str = 'USED_EMBEDDING'
                                
                                edge_type = edge_type_map.get(edge_type_str, DEPENDS_ON)
                                revision.create_edge(dep_revision, edge_type, metadata={
                                    'node_type': node_type,
                                    'asset_type': asset_type,
                                    'file_path': file_path,
                                })
                                dep_info['status'] = 'linked'
                        except Exception as edge_err:
                            print(f"[Kumiho] Warning: Could not process dependency: {edge_err}")
                
                output_kref = revision.kref
                print(f"[Kumiho] ✓ Successfully registered video: {output_kref}")
                if preview_gif_path:
                    print(f"[Kumiho] ✓ Preview artifact included: preview")
            else:
                print(f"[Kumiho] ERROR: Could not create/get space for category: {category}")
                
        except Exception as e:
            import traceback
            print(f"[Kumiho] ERROR: Could not register video with Kumiho Cloud: {e}")
            print(f"[Kumiho] Traceback: {traceback.format_exc()}")
        
        return output_kref


# =============================================================================
# KumihoSaveImage Node
# =============================================================================

class KumihoSaveImage(_KumihoSaveBase):
    """
    Save images to disk and register with Kumiho Cloud.
    
    This node:
    1. Saves images to ComfyUI output folder
    2. Embeds workflow metadata in PNG files
    3. Registers with Kumiho Cloud (if connected)
    4. Creates lineage edges for reproducibility
    
    Works like ComfyUI's Save Image but with Kumiho integration.
    """
    
    CATEGORY = "Kumiho/Save"
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Project name (blank = auto)"
                }),
                "images": ("IMAGE",),
                "category": ("STRING", {
                    "default": "outputs/images",
                    "multiline": False,
                    "placeholder": "Space path (e.g., outputs/portraits)"
                }),
                "item_name": ("STRING", {
                    "default": "output",
                    "multiline": False,
                    "placeholder": "Name for the item (revisions will be auto-versioned)"
                }),
                "item_kind": ("STRING", {
                    "default": "image",
                    "multiline": False,
                    "placeholder": "Item kind (e.g., image, checkpoint)"
                }),
            },
            "optional": {
                "description": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Description of this output"
                }),
                "tags": ("STRING", {
                    "default": None,
                    "multiline": False,
                    "placeholder": "Comma-separated tags"
                }),
                "file_path": ("STRING", {
                    "default": None,
                    "multiline": False,
                    "placeholder": "Optional: Custom file path to save (overrides auto-generated path)"
                }),
                "auto_register_deps": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Auto-register dependencies",
                    "label_off": "Skip dependency registration"
                }),
                "create_lineage": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Create lineage edges",
                    "label_off": "No lineage"
                }),
                "image_format": (["png", "jpg", "webp"], {
                    "default": "png"
                }),
                "quality": ("INT", {
                    "default": 95,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                }),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            }
        }
    
    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("images", "revision_kref", "lineage_json")
    OUTPUT_NODE = True
    FUNCTION = "register"
    
    def register(self, images: torch.Tensor, item_name: str, item_kind: str, category: str,
                 project: str = "", description: str = "", tags: Optional[str] = None,
                 auto_register_deps: bool = True, create_lineage: bool = True,
                 image_format: str = "png", quality: int = 95,
                 file_path: Optional[str] = None,
                 prompt: Optional[Dict] = None, extra_pnginfo: Optional[Dict] = None, unique_id: Optional[str] = None):
        """Register images to Kumiho Cloud with lineage."""
        project = project.strip() or get_configured_project()
        effective_kind = item_kind.strip() if item_kind and item_kind.strip() else "image"
        
        # Parse workflow (extracts dependencies, seed values, and generation settings)
        workflow_data, dependencies, seeds, generation_settings = self._parse_workflow(prompt, extra_pnginfo)
        
        # Build media info
        media_info = {
            'batch_size': images.shape[0],
            'resolution': f"{images.shape[2]}x{images.shape[1]}",
            'format': image_format,
        }
        
        # Build lineage
        lineage = self._build_lineage(
            project, category, item_name, effective_kind,
            media_info, dependencies, seeds, generation_settings,
            auto_register_deps, create_lineage,
            prompt, extra_pnginfo
        )
        
        # =================================================================
        # Save images to disk
        # =================================================================
        saved_paths = []
        output_dir = folder_paths.get_output_directory() if HAS_COMFYUI else tempfile.gettempdir()
        
        # Check if custom file_path is provided
        use_custom_path = file_path and file_path.strip()
        
        if use_custom_path:
            # Use custom path - ensure directory exists
            custom_path = file_path.strip()
            full_output_dir = os.path.dirname(custom_path)
            if full_output_dir:
                os.makedirs(full_output_dir, exist_ok=True)
        else:
            # Create subfolder based on category
            category_path = category.replace('/', os.sep)
            full_output_dir = os.path.join(output_dir, "kumiho", category_path)
            os.makedirs(full_output_dir, exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for idx, image in enumerate(images):
            # Convert tensor to PIL Image
            img_np = (image.cpu().numpy() * 255).astype(np.uint8)
            pil_image = Image.fromarray(img_np)
            
            if use_custom_path:
                # Use custom file path
                if images.shape[0] > 1:
                    # For batch, append index to custom path
                    base, ext_part = os.path.splitext(file_path.strip())
                    current_file_path = f"{base}_{idx:04d}{ext_part}"
                else:
                    current_file_path = file_path.strip()
            else:
                # Build filename (files use timestamp, but item name does not)
                ext = image_format
                if images.shape[0] > 1:
                    filename = f"{item_name}_{timestamp}_{idx:04d}.{ext}"
                else:
                    filename = f"{item_name}_{timestamp}.{ext}"
                current_file_path = os.path.join(full_output_dir, filename)
            
            # Determine save format from file extension if custom path, otherwise use image_format
            if use_custom_path:
                ext_from_path = os.path.splitext(current_file_path)[1].lower().lstrip('.')
                save_format = ext_from_path if ext_from_path in ['png', 'jpg', 'jpeg', 'webp'] else image_format
            else:
                save_format = image_format
            
            # Save based on format
            if save_format == "png":
                # Save with workflow metadata embedded in PNG
                metadata = None
                if extra_pnginfo:
                    from PIL import PngImagePlugin
                    metadata = PngImagePlugin.PngInfo()
                    for key, value in extra_pnginfo.items():
                        metadata.add_text(key, json.dumps(value) if isinstance(value, dict) else str(value))
                pil_image.save(current_file_path, pnginfo=metadata)
            elif save_format in ["jpg", "jpeg"]:
                pil_image.save(current_file_path, quality=quality)
            elif save_format == "webp":
                pil_image.save(current_file_path, quality=quality)
            else:
                # Fallback to PNG
                pil_image.save(current_file_path)
            
            saved_paths.append(current_file_path)
            print(f"[Kumiho] Saved image: {current_file_path}")
        
        # Update lineage with saved paths
        lineage['output']['saved_paths'] = saved_paths
        lineage['output']['output_directory'] = full_output_dir
        
        # Register with Kumiho
        output_kref = self._register_with_kumiho(
            project, category, item_name, effective_kind,
            saved_paths, lineage, description, tags, create_lineage, timestamp
        )
        lineage['output']['kref'] = output_kref
        
        # Convert lineage to JSON
        lineage_json = json.dumps(lineage, indent=2, default=str)
        
        # Send notification to frontend
        if HAS_PROMPT_SERVER:
            PromptServer.instance.send_sync("kumiho.register.complete", {
                "kref": output_kref,
                "saved_paths": saved_paths,
                "dependencies_count": len(dependencies),
                "edges_count": len(lineage.get('edges', [])),
                "type": "image",
            })
        
        # Build preview for ComfyUI UI
        # ComfyUI expects relative path from output directory for image preview
        preview_results = []
        for saved_path in saved_paths:
            # Get relative path from output directory
            rel_path = os.path.relpath(saved_path, output_dir)
            # Use forward slashes for web
            rel_path = rel_path.replace(os.sep, '/')
            
            preview_results.append({
                "filename": os.path.basename(saved_path),
                "subfolder": os.path.dirname(rel_path),
                "type": "output",
            })
        
        # Return with UI preview - uses "images" key for image preview in ComfyUI
        return {
            "ui": {
                "images": preview_results,
            },
            "result": (images, output_kref, lineage_json)
        }


# =============================================================================
# KumihoSaveVideo Node
# =============================================================================

class KumihoSaveVideo(_KumihoSaveBase):
    """
    Save video to disk and register with Kumiho Cloud.
    
    This node:
    1. Converts image batch (frames) to video file
    2. Saves to ComfyUI output folder (mp4, webm, or gif)
    3. Registers with Kumiho Cloud (if connected)
    4. Creates lineage edges for reproducibility
    
    Requires FFmpeg for mp4/webm, falls back to GIF if unavailable.
    """
    
    CATEGORY = "Kumiho/Save"
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Project name (blank = auto)"
                }),
                "images": ("IMAGE",),  # Batch of frames [B,H,W,C]
                "category": ("STRING", {
                    "default": "outputs/videos",
                    "multiline": False,
                    "placeholder": "Space path (e.g., outputs/animations)"
                }),
                "item_name": ("STRING", {
                    "default": "video_output",
                    "multiline": False,
                    "placeholder": "Name for the item (revisions will be auto-versioned)"
                }),
                "item_kind": ("STRING", {
                    "default": "video",
                    "multiline": False,
                    "placeholder": "Item kind (e.g., video, animation)"
                }),
                "fps": ("FLOAT", {
                    "default": 24.0,
                    "min": 1.0,
                    "max": 120.0,
                    "step": 1.0,
                }),
            },
            "optional": {
                "description": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Description of this output"
                }),
                "tags": ("STRING", {
                    "default": None,
                    "multiline": False,
                    "placeholder": "Comma-separated tags"
                }),
                "file_path": ("STRING", {
                    "default": None,
                    "multiline": False,
                    "placeholder": "Optional: Custom file path to save (overrides auto-generated path)"
                }),
                "auto_register_deps": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Auto-register dependencies",
                    "label_off": "Skip dependency registration"
                }),
                "create_lineage": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Create lineage edges",
                    "label_off": "No lineage"
                }),
                "video_format": (["mp4", "webm", "gif"], {
                    "default": "mp4"
                }),
                "quality": ("INT", {
                    "default": 95,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "tooltip": "Video quality (CRF for mp4/webm, affects GIF palette)"
                }),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            }
        }
    
    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("images", "revision_kref", "lineage_json")
    OUTPUT_NODE = True
    FUNCTION = "register"
    
    def register(self, images: torch.Tensor, item_name: str, item_kind: str, category: str,
                 fps: float = 24.0, project: str = "", description: str = "", tags: Optional[str] = None,
                 auto_register_deps: bool = True, create_lineage: bool = True,
                 video_format: str = "mp4", quality: int = 95,
                 file_path: Optional[str] = None,
                 prompt: Optional[Dict] = None, extra_pnginfo: Optional[Dict] = None, unique_id: Optional[str] = None):
        """Register video to Kumiho Cloud with lineage."""
        project = project.strip() or get_configured_project()
        effective_kind = item_kind.strip() if item_kind and item_kind.strip() else "video"
        
        # Parse workflow (extracts dependencies, seed values, and generation settings)
        workflow_data, dependencies, seeds, generation_settings = self._parse_workflow(prompt, extra_pnginfo)
        
        # Build media info
        frame_count = images.shape[0]
        duration = frame_count / fps
        media_info = {
            'frame_count': frame_count,
            'resolution': f"{images.shape[2]}x{images.shape[1]}",
            'fps': fps,
            'duration': f"{duration:.2f}s",
            'format': video_format,
        }
        
        # Build lineage
        lineage = self._build_lineage(
            project, category, item_name, effective_kind,
            media_info, dependencies, seeds, generation_settings,
            auto_register_deps, create_lineage,
            prompt, extra_pnginfo
        )
        
        # =================================================================
        # Save video to disk
        # =================================================================
        saved_paths = []
        output_dir = folder_paths.get_output_directory() if HAS_COMFYUI else tempfile.gettempdir()
        
        # Check if custom file_path is provided
        use_custom_path = file_path and file_path.strip()
        
        if use_custom_path:
            # Use custom path - ensure directory exists
            custom_path = file_path.strip()
            full_output_dir = os.path.dirname(custom_path)
            if full_output_dir:
                os.makedirs(full_output_dir, exist_ok=True)
            # Determine format from file extension
            ext_from_path = os.path.splitext(custom_path)[1].lower().lstrip('.')
            actual_format = ext_from_path if ext_from_path in ['mp4', 'webm', 'gif'] else video_format
            video_file_path = custom_path
        else:
            # Create subfolder based on category
            category_path = category.replace('/', os.sep)
            full_output_dir = os.path.join(output_dir, "kumiho", category_path)
            os.makedirs(full_output_dir, exist_ok=True)
            # Generate filename with timestamp (file has timestamp, item name does not)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{item_name}_{timestamp}.{video_format}"
            video_file_path = os.path.join(full_output_dir, filename)
            actual_format = video_format
        
        # Convert frames to video
        if actual_format == "gif":
            # Save as GIF using PIL
            frames = []
            for image in images:
                img_np = (image.cpu().numpy() * 255).astype(np.uint8)
                pil_image = Image.fromarray(img_np)
                frames.append(pil_image)
            
            if frames:
                frames[0].save(
                    video_file_path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=int(1000 / fps),
                    loop=0
                )
                print(f"[Kumiho] Saved GIF: {video_file_path}")
        else:
            # Try to use ffmpeg for mp4/webm
            try:
                import subprocess
                
                # Save frames to temp directory
                temp_dir = tempfile.mkdtemp(prefix="kumiho_video_")
                frame_paths = []
                
                for idx, image in enumerate(images):
                    img_np = (image.cpu().numpy() * 255).astype(np.uint8)
                    pil_image = Image.fromarray(img_np)
                    frame_path = os.path.join(temp_dir, f"frame_{idx:06d}.png")
                    pil_image.save(frame_path)
                    frame_paths.append(frame_path)
                
                # Calculate CRF from quality (inverse relationship)
                crf = max(0, min(51, int(51 - (quality * 0.51))))
                
                # Build ffmpeg command
                input_pattern = os.path.join(temp_dir, "frame_%06d.png")
                
                if actual_format == "mp4":
                    cmd = [
                        "ffmpeg", "-y",
                        "-framerate", str(fps),
                        "-i", input_pattern,
                        "-c:v", "libx264",
                        "-crf", str(crf),
                        "-pix_fmt", "yuv420p",
                        video_file_path
                    ]
                else:  # webm
                    cmd = [
                        "ffmpeg", "-y",
                        "-framerate", str(fps),
                        "-i", input_pattern,
                        "-c:v", "libvpx-vp9",
                        "-crf", str(crf),
                        "-b:v", "0",
                        video_file_path
                    ]
                
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    print(f"[Kumiho] Saved video: {video_file_path}")
                else:
                    print(f"[Kumiho] FFmpeg error: {result.stderr}")
                    # Fallback to GIF
                    video_file_path = video_file_path.replace(f".{actual_format}", ".gif")
                    frames = [Image.fromarray((img.cpu().numpy() * 255).astype(np.uint8)) for img in images]
                    frames[0].save(video_file_path, save_all=True, append_images=frames[1:], duration=int(1000/fps), loop=0)
                    print(f"[Kumiho] Fallback to GIF: {video_file_path}")
                
                # Cleanup temp frames
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
                
            except FileNotFoundError:
                print("[Kumiho] FFmpeg not found, falling back to GIF")
                video_file_path = video_file_path.replace(f".{actual_format}", ".gif")
                frames = [Image.fromarray((img.cpu().numpy() * 255).astype(np.uint8)) for img in images]
                frames[0].save(video_file_path, save_all=True, append_images=frames[1:], duration=int(1000/fps), loop=0)
        
        saved_paths.append(video_file_path)
        
        # Create GIF preview for mp4/webm (ComfyUI UI only supports GIF preview)
        preview_gif_path = None
        final_format = os.path.splitext(video_file_path)[1].lstrip('.')
        if final_format in ["mp4", "webm"]:
            preview_gif_path = video_file_path.rsplit('.', 1)[0] + "_preview.gif"
            try:
                # Create a smaller/faster GIF for preview (subsample frames if many)
                preview_frames = []
                step = max(1, len(images) // 30)  # Limit to ~30 frames for preview
                for i in range(0, len(images), step):
                    img_np = (images[i].cpu().numpy() * 255).astype(np.uint8)
                    pil_image = Image.fromarray(img_np)
                    # Optionally resize for faster preview
                    max_size = 512
                    if pil_image.width > max_size or pil_image.height > max_size:
                        ratio = min(max_size / pil_image.width, max_size / pil_image.height)
                        new_size = (int(pil_image.width * ratio), int(pil_image.height * ratio))
                        pil_image = pil_image.resize(new_size, Image.Resampling.LANCZOS)
                    preview_frames.append(pil_image)
                
                if preview_frames:
                    # Adjust duration for subsampled frames
                    preview_duration = int((1000 / fps) * step)
                    preview_frames[0].save(
                        preview_gif_path,
                        save_all=True,
                        append_images=preview_frames[1:],
                        duration=preview_duration,
                        loop=0
                    )
                    print(f"[Kumiho] Created preview GIF: {preview_gif_path}")
            except Exception as gif_err:
                print(f"[Kumiho] Warning: Could not create preview GIF: {gif_err}")
                preview_gif_path = None
        
        # Update lineage with saved paths (include preview if created)
        lineage['output']['saved_paths'] = saved_paths
        lineage['output']['output_directory'] = full_output_dir
        if preview_gif_path and os.path.exists(preview_gif_path):
            lineage['output']['preview_path'] = preview_gif_path
        
        # Register with Kumiho (pass preview_gif_path for additional artifact)
        output_kref = self._register_video_with_kumiho(
            project, category, item_name, effective_kind,
            saved_paths, preview_gif_path, lineage, description, tags, create_lineage, timestamp
        )
        lineage['output']['kref'] = output_kref
        
        # Convert lineage to JSON
        lineage_json = json.dumps(lineage, indent=2, default=str)
        
        # Send notification to frontend
        if HAS_PROMPT_SERVER:
            PromptServer.instance.send_sync("kumiho.register.complete", {
                "kref": output_kref,
                "saved_paths": saved_paths,
                "preview_path": preview_gif_path if preview_gif_path else None,
                "dependencies_count": len(dependencies),
                "edges_count": len(lineage.get('edges', [])),
                "type": "video",
            })
        
        # Return results - video preview not supported in node UI, but available in Media Assets
        return (images, output_kref, lineage_json)


# =============================================================================
# KumihoLoadAsset Node  
# =============================================================================

class KumihoLoadAsset:
    """
    Load assets from Kumiho Cloud using kref URIs or dropdown selection.
    
    Supports two modes:
    1. Direct kref input: Enter a kref URI directly
    2. Component entry: Provide project/space/item fields
    
    kref format: kref://project/space/item.kind?r=revision&a=artifact_type
    
    Examples:
    - kref://ComfyUI@KumihoClouds/lora/flux/Eye-Lora.lora?r=1&a=lora
    - kref://ComfyUI@KumihoClouds/checkpoint/flux/flux1-schnell.checkpoint?r=latest&a=fp8
    """
    
    CATEGORY = "Kumiho/Load"
    
    @classmethod
    def INPUT_TYPES(cls):
        catalog = get_asset_catalog()
        project_options = ["[Auto]"] + catalog.get_projects()
        
        return {
            "required": {
                "item_name": ("STRING", {
                    "default": "image",
                    "multiline": False,
                    "placeholder": "Item name (e.g., my_asset)"
                }),
                "item_kind": ("STRING", {
                    "default": "image",
                    "multiline": False,
                    "placeholder": "Item kind (e.g., image, video, checkpoint)"
                }),
            },
            "optional": {
                "project": (project_options, {
                    "default": "[Auto]"
                }),
                "space": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Space path (e.g., checkpoint/flux)"
                }),
                # Direct kref mode input (overrides all above if provided)
                "kref_uri": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "kref://project/space/item.kind?r=revision"
                }),
                # Common options
                "tag / revision": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Revision tag or number (blank = latest)"
                }),
                "artifact_name": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Artifact name (e.g., video, preview)"
                }),
                "fallback_file_path": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Local fallback file path if kref fails"
                }),
            }
        }
    
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("file_path", "kref", "metadata")
    FUNCTION = "load_asset"
    
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        """Force re-execution when inputs change."""
        return float("nan")
    
    def load_asset(self, item_name: str, item_kind: str,
                   project: str = "", space: str = "", kref_uri: str = "", 
                   artifact_name: str = "", fallback_file_path: str = "",
                   tag_revision: str = "", **kwargs):
        """
        Load an asset from Kumiho Cloud and return its local file path.
        
        Args:
            item_name: Item name to load
            item_kind: Item kind (image, video, checkpoint, etc.)
            space: Space path (e.g., "input", "outputs")
            kref_uri: Direct kref URI (overrides component inputs if provided)
            tag_revision: Revision tag or number to load (default: latest)
            artifact_name: Specific artifact name to retrieve
            fallback_file_path: Local file path to use if kref resolution fails
        
        Returns:
            (file_path, kref, metadata): Tuple of resolved file path, kref URI, and metadata JSON
        """
        # Allow per-node project override, otherwise use configured default
        selected_project = project.strip()
        if selected_project == "[Auto]":
            selected_project = ""
        default_project = selected_project or get_configured_project()
        
        raw_tag_value = kwargs.get("tag / revision", tag_revision)
        if raw_tag_value is None:
            tag_value = ""
        elif isinstance(raw_tag_value, str):
            tag_value = raw_tag_value.strip()
        else:
            tag_value = str(raw_tag_value).strip()
        tag_for_resolution = tag_value or "latest"
        
        metadata = {
            'item_name': item_name,
            'item_kind': item_kind,
            'project': default_project,
            'tag': tag_value,
            'artifact_name': artifact_name,
            'status': 'pending'
        }
        
        effective_kref = ""
        
        # Priority 1: Direct kref_uri takes precedence
        if kref_uri and kref_uri.strip():
            effective_kref = kref_uri.strip()
            metadata['input_mode'] = 'kref_uri'
        else:
            # Priority 2: Build kref from components
            if not item_name:
                if fallback_file_path:
                    metadata['status'] = 'fallback'
                    metadata['message'] = 'No item name provided'
                    return (fallback_file_path, "", json.dumps(metadata))
                else:
                    metadata['status'] = 'error'
                    metadata['error'] = 'Item name is required'
                    return ("", "", json.dumps(metadata))
            
            # Determine effective space from dropdown or item_kind
            effective_space = ""
            if space:
                effective_space = space.strip()
            elif item_kind:
                # Use item_kind to determine default space
                asset_info = ASSET_TYPE_MAP.get(item_kind, {})
                effective_space = asset_info.get('space', item_kind)
            else:
                # Default to outputs
                effective_space = "outputs"
            
            # Determine kind
            kind = item_kind.strip() if item_kind.strip() else "asset"
            
            # Build kref: kref://project/space/item_name.kind
            effective_kref = build_kref(default_project, effective_space, item_name, kind)
            metadata['input_mode'] = 'components'
            metadata['resolved_project'] = default_project
            metadata['resolved_space'] = effective_space
            metadata['resolved_kind'] = kind
        
        metadata['effective_kref'] = effective_kref
        
        if not effective_kref:
            if fallback_file_path:
                metadata['status'] = 'fallback'
                return (fallback_file_path, "", json.dumps(metadata))
            else:
                metadata['status'] = 'error'
                metadata['error'] = 'Could not build kref URI'
                return ("", "", json.dumps(metadata))
        
        parsed = None
        try:
            # Parse the kref URI
            parsed = parse_kref(effective_kref)
            
            # Override revision if provided
            if tag_value:
                parsed['revision'] = tag_value
            elif '?r=' in effective_kref or '&r=' in effective_kref:
                tag_for_resolution = parsed.get('revision') or tag_for_resolution
            
            metadata['parsed_kref'] = parsed
            
            # Resolve kref to actual file path using artifact location
            resolved_path = self._resolve_kref_to_path(effective_kref, parsed, artifact_name, tag_for_resolution)
            
            if resolved_path:
                metadata['status'] = 'resolved'
                metadata['resolved_path'] = resolved_path
                return_kref = self._build_output_kref(effective_kref, parsed, tag_value, artifact_name)
                return (resolved_path, return_kref, json.dumps(metadata, indent=2))
            else:
                # Resolution failed
                if fallback_file_path:
                    metadata['status'] = 'fallback'
                    metadata['message'] = 'kref resolution failed, using fallback'
                    return_kref = self._build_output_kref(effective_kref, parsed, tag_value, artifact_name)
                    return (fallback_file_path, return_kref, json.dumps(metadata, indent=2))
                else:
                    metadata['status'] = 'unresolved'
                    metadata['message'] = 'Could not resolve kref to file path'
                    return_kref = self._build_output_kref(effective_kref, parsed, tag_value, artifact_name)
                    return ("", return_kref, json.dumps(metadata, indent=2))
            
        except Exception as e:
            metadata['status'] = 'error'
            metadata['error'] = str(e)
            
            if fallback_file_path:
                metadata['status'] = 'fallback'
                return_kref = self._build_output_kref(effective_kref, parsed, tag_value, artifact_name)
                return (fallback_file_path, return_kref, json.dumps(metadata))
            
            return_kref = self._build_output_kref(effective_kref, parsed, tag_value, artifact_name)
            return ("", return_kref, json.dumps(metadata))
    
    def _resolve_kref_to_path(self, kref: str, parsed: Dict, artifact_name: str = "", revision_str: str = "latest") -> Optional[str]:
        """
        Resolve a kref URI to an actual file path using Kumiho SDK.
        
        Uses BYO Storage model - the artifact's location field contains the actual path.
        """
        if not HAS_KUMIHO_SDK:
            return None
        
        try:
            project = parsed.get('project', '')
            space = parsed.get('space', '')
            item_name = parsed.get('item', '')
            kind = parsed.get('kind', '')
            
            # Build the item kref (without revision/artifact params)
            item_kref = f"kref://{project}/{space}/{item_name}.{kind}"
            
            print(f"[Kumiho] Resolving asset: {item_kref}")
            
            # Try to get the item
            try:
                item = kumiho.get_item(item_kref)
            except Exception as item_err:
                print(f"[Kumiho] Item not found: {item_kref} ({item_err})")
                return None
            
            if not item:
                print(f"[Kumiho] Item not found: {item_kref}")
                return None
            
            # Get the specified revision or latest
            revision = None
            if revision_str and revision_str != 'latest':
                try:
                    revision_num = int(revision_str)
                    revisions = item.get_revisions()
                    for rev in revisions:
                        if rev.number == revision_num:
                            revision = rev
                            break
                except (ValueError, TypeError):
                    # Not a number, try as tag
                    try:
                        revision = item.get_revision_by_tag(revision_str)
                    except Exception:
                        pass
            
            if not revision:
                # Get latest revision
                revisions = item.get_revisions()
                revision = revisions[0] if revisions else None
            
            if not revision:
                print(f"[Kumiho] No revisions found for: {item_kref}")
                return None
            
            print(f"[Kumiho] Using revision: {revision.number}")
            
            # Get artifacts from the revision
            artifacts = revision.get_artifacts()
            if not artifacts:
                print(f"[Kumiho] No artifacts found for revision {revision.number}")
                return None
            
            # Find matching artifact by name, or return first one
            for artifact in artifacts:
                if artifact_name:
                    # Match by artifact name
                    if artifact.name == artifact_name:
                        location = getattr(artifact, 'location', None)
                        if location:
                            print(f"[Kumiho] Resolved artifact '{artifact.name}' -> {location}")
                            return location
                else:
                    # Return first artifact's location (skip 'preview' if there's another option)
                    if len(artifacts) > 1 and artifact.name == 'preview':
                        continue
                    location = getattr(artifact, 'location', None)
                    if location:
                        print(f"[Kumiho] Resolved artifact '{artifact.name}' -> {location}")
                        return location
            
            # If we're looking for a specific artifact and didn't find it, try first one
            if artifact_name and artifacts:
                location = getattr(artifacts[0], 'location', None)
                if location:
                    print(f"[Kumiho] Artifact '{artifact_name}' not found, using '{artifacts[0].name}' -> {location}")
                    return location
            
            print(f"[Kumiho] Could not resolve any artifact location")
            return None
            
        except Exception as e:
            print(f"[Kumiho] Error resolving kref {kref}: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _build_output_kref(self, effective_kref: str, parsed: Optional[Dict], tag_value: str, artifact_name: str) -> str:
        if not parsed:
            return effective_kref
        kref_has_revision = '?r=' in effective_kref or '&r=' in effective_kref
        revision = tag_value if tag_value else (parsed.get('revision') if kref_has_revision else None)
        artifact = artifact_name.strip() if artifact_name.strip() else parsed.get('artifact_type')
        return build_kref(parsed['project'], parsed['space'], parsed['item'], parsed['kind'],
                          revision=revision, artifact_type=artifact)


# =============================================================================
# Graph/Helper Nodes
# =============================================================================

class KumihoCreateEdge:
    """
    Create a dependency edge between two Kumiho revisions.
    
    This node establishes relationships in the Kumiho dependency
    graph, enabling impact analysis and lineage tracking.
    """
    
    CATEGORY = "Kumiho/Graph"
    OUTPUT_NODE = True
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_kref": ("STRING", {
                    "default": "",
                    "multiline": False,
                }),
                "target_kref": ("STRING", {
                    "default": "",
                    "multiline": False,
                }),
                "edge_type": (["DEPENDS_ON", "DERIVED_FROM", "REFERENCED", "CONTAINS", "CREATED_FROM", "USED_MODEL", "USED_LORA", "USED_INPUT"],),
            },
            "optional": {
                "metadata_json": ("STRING", {
                    "default": "{}",
                    "multiline": True,
                }),
            }
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("edge_id",)
    FUNCTION = "create_edge"
    
    def create_edge(self, source_kref: str, target_kref: str, edge_type: str,
                    metadata_json: str = "{}"):
        """
        Create a dependency edge between two revisions.
        """
        # TODO: Implement actual Kumiho API integration
        edge_id = f"edge_{hashlib.md5(f'{source_kref}_{target_kref}_{edge_type}'.encode()).hexdigest()[:8]}"
        
        return (edge_id,)


class KumihoTagRevision:
    """
    Apply a tag to a Kumiho revision.
    
    Common tags: 'approved', 'published', 'ready-for-review', 'wip'
    """
    
    CATEGORY = "Kumiho/Graph"
    OUTPUT_NODE = True
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "revision_kref": ("STRING", {
                    "default": "",
                    "multiline": False,
                }),
                "tag": ("STRING", {
                    "default": "comfyui-output",
                    "multiline": False,
                }),
            },
        }
    
    RETURN_TYPES = ("BOOLEAN",)
    RETURN_NAMES = ("success",)
    FUNCTION = "tag_revision"
    
    def tag_revision(self, revision_kref: str, tag: str):
        """Apply a tag to a revision."""
        # TODO: Implement actual Kumiho API integration
        return (True,)


class KumihoSearchItems:
    """
    Search for items in Kumiho Cloud using kumiho.item_search().
    
    Returns lists of matching item krefs and file paths that can be 
    iterated over with other nodes.
    
    Uses the default project as context_filter if none specified.
    
    Parameters match the Python SDK:
    - name_filter: Filter by item name (supports wildcards like 'flux*')
    - kind_filter: Filter by item kind (image, video, checkpoint, etc.)
    - context_filter: Filter by project/space path (default: current project/*)
    """
    
    CATEGORY = "Kumiho/Search"
    
    @classmethod
    def INPUT_TYPES(cls):
        # Kind options for filtering - matches SDK supported kinds
        kind_options = [
            "",  # No filter (all kinds)
            "image",
            "video",
            "audio",
            "checkpoint",
            "lora",
            "vae",
            "controlnet",
            "embedding",
            "diffusion_model",
            "clip",
            "clip_vision",
            "text_encoder",
            "upscaler",
            "ipadapter",
            "style_model",
            "gligen",
            "animatediff_model",
            "motion_lora",
            "mesh",
            "text",
            "data",
            "asset",
            "mask",
        ]
        catalog = get_asset_catalog()
        project_options = ["[Auto]"] + catalog.get_projects()
        
        return {
            "required": {
            },
            "optional": {
                "project": (project_options, {
                    "default": "[Auto]"
                }),
                "name_filter": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Item name pattern (e.g., 'flux*', '*portrait*')"
                }),
                "kind_filter": (kind_options, {
                    "default": ""
                }),
                "context_filter": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Project/space filter (default: selected project/*)"
                }),
                "limit": ("INT", {
                    "default": 50,
                    "min": 1,
                    "max": 500,
                    "step": 10,
                }),
            }
        }
    
    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("krefs", "file_paths", "results_json", "count")
    OUTPUT_IS_LIST = (True, True, False, False)
    FUNCTION = "search_items"
    
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        """Force re-execution when inputs change."""
        return float("nan")
    
    def search_items(self, project: str = "[Auto]", name_filter: str = "", kind_filter: str = "", 
                     context_filter: str = "", limit: int = 50):
        """
        Search for items in Kumiho Cloud using kumiho.item_search().
        
        Args:
            name_filter: Name pattern to search for (supports wildcards like 'flux*')
            kind_filter: Filter by item kind (image, video, checkpoint, etc.)
            context_filter: Space path filter (defaults to current project/*)
            project: Project to search within (default: auto)
            limit: Maximum number of results to return
            
        Returns:
            Tuple of (krefs_list, file_paths_list, results_json, count)
        """
        krefs = []
        file_paths = []
        results_data = {
            'items': [],
            'count': 0,
            'filters': {
                'project': project,
                'name_filter': name_filter,
                'kind_filter': kind_filter,
                'context_filter': context_filter,
            },
            'status': 'pending'
        }
        
        if not HAS_KUMIHO_SDK:
            results_data['status'] = 'error'
            results_data['error'] = 'Kumiho SDK not available'
            print("[Kumiho] SDK not available for search")
            return (krefs, file_paths, json.dumps(results_data, indent=2), 0)
        
        try:
            selected_project = project.strip()
            if selected_project == "[Auto]":
                selected_project = ""
            base_project = selected_project or get_configured_project()
            effective_context = context_filter.strip() if context_filter.strip() else base_project
            results_data['filters']['effective_context'] = effective_context
            
            print(f"[Kumiho] Searching items: name_filter='{name_filter}', kind_filter='{kind_filter}', context_filter='{effective_context}'")
            
            # Call kumiho.item_search with SDK parameter names
            items = kumiho.item_search(
                name_filter=name_filter if name_filter else None,
                kind_filter=kind_filter if kind_filter else None,
                context_filter=effective_context if effective_context else None,
            )
            
            if items:
                # Limit results
                items = items[:limit]
                
                for item in items:
                    # Try to resolve file path from latest revision's artifact
                    file_path = ""
                    try:
                        # Get latest revision
                        revisions = item.get_revisions()
                        if revisions:
                            latest_rev = revisions[0]
                            artifacts = latest_rev.get_artifacts()
                            if artifacts:
                                # Get first artifact's location (skip 'preview' if there are others)
                                for artifact in artifacts:
                                    if len(artifacts) > 1 and artifact.name == 'preview':
                                        continue
                                    location = getattr(artifact, 'location', None)
                                    if location:
                                        file_path = location
                                        break
                    except Exception as rev_err:
                        print(f"[Kumiho] Warning: Could not get file path for {item.kref}: {rev_err}")
                    
                    # Only include items that have a valid file path
                    if not file_path:
                        print(f"[Kumiho] Skipping item without artifact: {item.kref}")
                        continue
                    
                    # Use item's kref URI directly
                    krefs.append(item.kref)
                    file_paths.append(file_path)
                    
                    # Add to results data
                    results_data['items'].append({
                        'name': item.name,
                        'kind': item.kind,
                        'kref': item.kref,
                        'file_path': file_path,
                        'project': item.project,
                        'space': item.space,
                    })
                
                results_data['count'] = len(krefs)
                results_data['status'] = 'success'
                print(f"[Kumiho] Search found {len(krefs)} items with valid artifacts")
            else:
                results_data['count'] = 0
                results_data['status'] = 'success'
                results_data['message'] = 'No items found'
                print("[Kumiho] Search returned no results")
                
        except Exception as e:
            results_data['status'] = 'error'
            results_data['error'] = str(e)
            print(f"[Kumiho] Search error: {e}")
            import traceback
            traceback.print_exc()
        
        return (krefs, file_paths, json.dumps(results_data, indent=2), len(krefs))


class KumihoGetDependencies:
    """
    Get all dependencies of a Kumiho revision.
    
    Traverses the dependency graph to find what a revision depends on.
    """
    
    CATEGORY = "Kumiho/Graph"
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "revision_kref": ("STRING", {
                    "default": "",
                    "multiline": False,
                }),
            },
            "optional": {
                "max_depth": ("INT", {
                    "default": 5,
                    "min": 1,
                    "max": 20,
                }),
                "edge_types": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Comma-separated: USED_MODEL,USED_LORA"
                }),
            }
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("dependencies_json",)
    FUNCTION = "get_dependencies"
    
    def get_dependencies(self, revision_kref: str, max_depth: int = 5,
                         edge_types: str = ""):
        """Get dependencies of a revision."""
        # TODO: Implement actual Kumiho API integration
        dependencies = '{"dependencies": [], "depth": 0}'
        
        return (dependencies,)


# =============================================================================
# Node Registration
# =============================================================================

NODE_CLASS_MAPPINGS = {
    # Primary nodes - Save (output nodes)
    "KumihoSaveImage": KumihoSaveImage,
    "KumihoSaveVideo": KumihoSaveVideo,
    
    # Primary nodes - Load
    "KumihoLoadAsset": KumihoLoadAsset,
    
    # Graph operations
    "KumihoCreateEdge": KumihoCreateEdge,
    "KumihoTagRevision": KumihoTagRevision,
    "KumihoSearchItems": KumihoSearchItems,
    "KumihoGetDependencies": KumihoGetDependencies,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "KumihoSaveImage": "🦊 Kumiho Save Image",
    "KumihoSaveVideo": "🦊 Kumiho Save Video",
    "KumihoLoadAsset": "🦊 Kumiho Load Asset",
    "KumihoCreateEdge": "🦊 Kumiho Create Edge",
    "KumihoTagRevision": "🦊 Kumiho Tag Revision",
    "KumihoSearchItems": "🦊 Kumiho Search Items",
    "KumihoGetDependencies": "🦊 Kumiho Get Dependencies",
}
