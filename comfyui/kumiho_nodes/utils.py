"""
Utility functions for Kumiho ComfyUI nodes.
"""

import os
import re
import json
import tempfile
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

import torch
import numpy as np
from PIL import Image


def parse_kref(kref: str) -> Dict[str, Any]:
    """
    Parse a kref:// URI into its components.
    
    Args:
        kref: A kref URI like 'kref://project/space/item.kind?r=1'
        
    Returns:
        Dictionary with parsed components:
        - project: The project name
        - space: The space path (can include nested paths)
        - item: The item name
        - kind: The item kind/type
        - revision: The revision number or tag (if specified)
    """
    # Remove the kref:// prefix
    if not kref.startswith('kref://'):
        raise ValueError(f"Invalid kref URI: {kref}")
    
    path = kref[7:]  # Remove 'kref://'
    
    # Extract revision if present
    revision = None
    if '?r=' in path:
        path, revision = path.split('?r=', 1)
    elif '?' in path:
        path, query = path.split('?', 1)
        # Parse query params
        params = dict(p.split('=') for p in query.split('&') if '=' in p)
        revision = params.get('r')
    
    # Split the path
    parts = path.split('/')
    if len(parts) < 3:
        raise ValueError(f"Invalid kref URI format: {kref}")
    
    project = parts[0]
    item_with_kind = parts[-1]
    space = '/'.join(parts[1:-1])
    
    # Extract item and kind
    if '.' not in item_with_kind:
        raise ValueError(f"Item must include kind (e.g., 'asset.model'): {kref}")
    
    item_parts = item_with_kind.rsplit('.', 1)
    item = item_parts[0]
    kind = item_parts[1]
    
    return {
        'project': project,
        'space': space,
        'item': item,
        'kind': kind,
        'revision': revision,
        'full_kref': kref
    }


def build_kref(project: str, space: str, item: str, kind: str, 
               revision: Optional[str] = None) -> str:
    """
    Build a kref:// URI from components.
    
    Args:
        project: The project name
        space: The space path
        item: The item name
        kind: The item kind/type
        revision: Optional revision number or tag
        
    Returns:
        A properly formatted kref URI
    """
    kref = f"kref://{project}/{space}/{item}.{kind}"
    if revision:
        kref += f"?r={revision}"
    return kref


def tensor_to_pil(tensor: torch.Tensor) -> Image.Image:
    """
    Convert a ComfyUI image tensor to a PIL Image.
    
    Args:
        tensor: A torch.Tensor with shape [B, H, W, C] or [H, W, C]
        
    Returns:
        PIL Image
    """
    # Handle batch dimension
    if tensor.dim() == 4:
        tensor = tensor[0]  # Take first image in batch
    
    # Convert to numpy and scale to 0-255
    np_image = tensor.cpu().numpy()
    np_image = np.clip(np_image * 255.0, 0, 255).astype(np.uint8)
    
    return Image.fromarray(np_image)


def pil_to_tensor(image: Image.Image) -> torch.Tensor:
    """
    Convert a PIL Image to a ComfyUI image tensor.
    
    Args:
        image: A PIL Image
        
    Returns:
        torch.Tensor with shape [1, H, W, C]
    """
    # Convert to RGB if necessary
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # Convert to numpy and normalize to 0-1
    np_image = np.array(image).astype(np.float32) / 255.0
    
    # Add batch dimension
    tensor = torch.from_numpy(np_image).unsqueeze(0)
    
    return tensor


def save_tensor_as_image(tensor: torch.Tensor, path: str, format: str = 'PNG') -> str:
    """
    Save a ComfyUI image tensor to a file.
    
    Args:
        tensor: The image tensor to save
        path: The output file path
        format: The image format (PNG, JPEG, etc.)
        
    Returns:
        The path where the image was saved
    """
    pil_image = tensor_to_pil(tensor)
    pil_image.save(path, format=format)
    return path


def load_image_as_tensor(path: str) -> torch.Tensor:
    """
    Load an image file as a ComfyUI image tensor.
    
    Args:
        path: Path to the image file
        
    Returns:
        torch.Tensor with shape [1, H, W, C]
    """
    pil_image = Image.open(path)
    return pil_to_tensor(pil_image)


def create_temp_file(suffix: str = '.png') -> str:
    """
    Create a temporary file and return its path.
    
    Args:
        suffix: The file suffix/extension
        
    Returns:
        Path to the temporary file
    """
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def get_kumiho_cache_dir() -> Path:
    """
    Get the Kumiho cache directory for storing downloaded assets.
    
    Returns:
        Path to the cache directory
    """
    cache_dir = Path(tempfile.gettempdir()) / 'kumiho_cache'
    cache_dir.mkdir(exist_ok=True)
    return cache_dir


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename by removing invalid characters.
    
    Args:
        filename: The original filename
        
    Returns:
        A sanitized filename safe for filesystem use
    """
    # Remove invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip('. ')
    return sanitized or 'unnamed'


def format_metadata(metadata: Dict[str, Any]) -> str:
    """
    Format metadata dictionary as a JSON string.
    
    Args:
        metadata: The metadata dictionary
        
    Returns:
        Pretty-printed JSON string
    """
    return json.dumps(metadata, indent=2, default=str)


def parse_metadata(metadata_str: str) -> Dict[str, Any]:
    """
    Parse a metadata JSON string.
    
    Args:
        metadata_str: JSON string
        
    Returns:
        Parsed dictionary
    """
    if not metadata_str or metadata_str.strip() == '':
        return {}
    
    try:
        return json.loads(metadata_str)
    except json.JSONDecodeError:
        return {}
