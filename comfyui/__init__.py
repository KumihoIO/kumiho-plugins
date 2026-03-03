"""
Kumiho ComfyUI Custom Nodes

Connect your ComfyUI workflows with Kumiho Cloud asset management system.
"""

from .kumiho_nodes.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# Web directory for custom JavaScript (if needed)
WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
