"""
Tests for the Kumiho ComfyUI workflow parser.

These tests verify:
- kref URI parsing
- Workflow dependency extraction
- Node detection
- Edge type mapping
"""

import pytest
import json
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from kumiho_nodes.nodes import (
    parse_kref,
    build_kref,
    detect_base_model,
    WorkflowParser,
    ASSET_TYPE_MAP,
    NODE_ASSET_MAP,
    EDGE_TYPES,
)


# =============================================================================
# Tests for kref parsing
# =============================================================================

class TestKrefParsing:
    """Tests for kref:// URI parsing."""
    
    def test_parse_basic_kref(self):
        """Test parsing a basic kref URI."""
        kref = "kref://ComfyUI@Test/lora/flux/style-lora.lora"
        result = parse_kref(kref)
        
        assert result['project'] == "ComfyUI@Test"
        assert result['space'] == "lora/flux"
        assert result['item'] == "style-lora"
        assert result['kind'] == "lora"
        assert result['revision'] == "latest"  # Default
        assert result['artifact_type'] is None
    
    def test_parse_kref_with_revision(self):
        """Test parsing kref with revision parameter."""
        kref = "kref://ComfyUI@Test/checkpoint/sdxl/model.checkpoint?r=3"
        result = parse_kref(kref)
        
        assert result['project'] == "ComfyUI@Test"
        assert result['space'] == "checkpoint/sdxl"
        assert result['item'] == "model"
        assert result['kind'] == "checkpoint"
        assert result['revision'] == "3"
    
    def test_parse_kref_with_artifact_type(self):
        """Test parsing kref with artifact type parameter."""
        kref = "kref://ComfyUI@Test/checkpoint/flux/flux-dev.checkpoint?r=latest&a=fp8"
        result = parse_kref(kref)
        
        assert result['revision'] == "latest"
        assert result['artifact_type'] == "fp8"
    
    def test_parse_kref_with_all_params(self):
        """Test parsing kref with all parameters."""
        kref = "kref://MyProject/models/characters/hero.model?r=published&a=high-res"
        result = parse_kref(kref)
        
        assert result['project'] == "MyProject"
        assert result['space'] == "models/characters"
        assert result['item'] == "hero"
        assert result['kind'] == "model"
        assert result['revision'] == "published"
        assert result['artifact_type'] == "high-res"
    
    def test_parse_invalid_kref_no_scheme(self):
        """Test that invalid kref without scheme raises error."""
        with pytest.raises(ValueError, match="Invalid kref URI"):
            parse_kref("ComfyUI@Test/lora/style.lora")
    
    def test_parse_invalid_kref_no_kind(self):
        """Test that kref without kind raises error."""
        with pytest.raises(ValueError, match="Item must include kind"):
            parse_kref("kref://ComfyUI@Test/lora/style")
    
    def test_parse_invalid_kref_short_path(self):
        """Test that kref with too short path raises error."""
        with pytest.raises(ValueError, match="Invalid kref path format"):
            parse_kref("kref://project/item.kind")


class TestKrefBuilding:
    """Tests for building kref URIs."""
    
    def test_build_basic_kref(self):
        """Test building a basic kref URI."""
        kref = build_kref("ComfyUI@Test", "lora/flux", "style", "lora")
        assert kref == "kref://ComfyUI@Test/lora/flux/style.lora"
    
    def test_build_kref_with_revision(self):
        """Test building kref with revision."""
        kref = build_kref("Project", "space", "item", "kind", revision="5")
        assert kref == "kref://Project/space/item.kind?r=5"
    
    def test_build_kref_with_artifact_type(self):
        """Test building kref with artifact type."""
        kref = build_kref("Project", "space", "item", "kind", artifact_type="fp8")
        assert kref == "kref://Project/space/item.kind?a=fp8"
    
    def test_build_kref_with_all_params(self):
        """Test building kref with all parameters."""
        kref = build_kref("Project", "space", "item", "kind", 
                         revision="latest", artifact_type="high-res")
        assert kref == "kref://Project/space/item.kind?r=latest&a=high-res"
    
    def test_roundtrip_kref(self):
        """Test that parse and build are inverses."""
        original = "kref://ComfyUI@Test/checkpoint/sdxl/model.checkpoint?r=3&a=fp16"
        parsed = parse_kref(original)
        rebuilt = build_kref(
            parsed['project'],
            parsed['space'],
            parsed['item'],
            parsed['kind'],
            parsed['revision'],
            parsed['artifact_type']
        )
        assert rebuilt == original


# =============================================================================
# Tests for base model detection
# =============================================================================

class TestBaseModelDetection:
    """Tests for detecting base model type from file paths."""
    
    def test_detect_flux(self):
        """Test detecting Flux models."""
        assert detect_base_model("/models/flux-dev-fp8.safetensors") == "flux"
        assert detect_base_model("C:\\ComfyUI\\models\\Flux\\model.ckpt") == "flux"
    
    def test_detect_sdxl(self):
        """Test detecting SDXL models."""
        assert detect_base_model("/models/sdxl-base.safetensors") == "sdxl"
        assert detect_base_model("/models/checkpoint-xl-v1.ckpt") == "sdxl"
    
    def test_detect_sd3(self):
        """Test detecting SD3 models."""
        assert detect_base_model("/models/sd3-medium.safetensors") == "sd3"
    
    def test_detect_sd15(self):
        """Test detecting SD 1.5 models."""
        assert detect_base_model("/models/sd15-pruned.safetensors") == "sd15"
        assert detect_base_model("/models/v1-5-pruned.ckpt") == "sd15"
        assert detect_base_model("/models/sd1.5-inpainting.safetensors") == "sd15"
    
    def test_detect_unknown(self):
        """Test unknown model detection."""
        assert detect_base_model("/models/custom-model.safetensors") == "unknown"


# =============================================================================
# Tests for workflow parsing
# =============================================================================

class TestWorkflowParser:
    """Tests for ComfyUI workflow parsing."""
    
    @pytest.fixture
    def sample_prompt_workflow(self):
        """Sample workflow in prompt format (from API)."""
        return {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {
                    "ckpt_name": "flux-dev-fp8.safetensors"
                }
            },
            "2": {
                "class_type": "LoraLoader",
                "inputs": {
                    "model": ["1", 0],
                    "clip": ["1", 1],
                    "lora_name": "style-lora.safetensors",
                    "strength_model": 0.8,
                    "strength_clip": 0.8
                }
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["2", 1],
                    "text": "a beautiful landscape"
                }
            },
            "4": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": "reference.png"
                }
            },
            "5": {
                "class_type": "VAELoader",
                "inputs": {
                    "vae_name": "sdxl-vae.safetensors"
                }
            }
        }
    
    def test_parse_prompt_workflow(self, sample_prompt_workflow):
        """Test parsing a prompt format workflow."""
        parser = WorkflowParser(sample_prompt_workflow)
        result = parser.parse()
        
        assert len(result['nodes']) == 5
        assert len(result['dependencies']) >= 4  # checkpoint, lora, image, vae
        
        # Check checkpoint dependency
        ckpt_deps = [d for d in result['dependencies'] if d['asset_type'] == 'checkpoints']
        assert len(ckpt_deps) == 1
        assert ckpt_deps[0]['value'] == "flux-dev-fp8.safetensors"
        
        # Check lora dependency
        lora_deps = [d for d in result['dependencies'] if d['asset_type'] == 'loras']
        assert len(lora_deps) == 1
        assert lora_deps[0]['value'] == "style-lora.safetensors"
    
    def test_parse_extracts_node_types(self, sample_prompt_workflow):
        """Test that node types are correctly extracted."""
        parser = WorkflowParser(sample_prompt_workflow)
        result = parser.parse()
        
        node_types = {n['type'] for n in result['nodes'].values()}
        assert "CheckpointLoaderSimple" in node_types
        assert "LoraLoader" in node_types
        assert "LoadImage" in node_types
    
    def test_dependency_has_required_fields(self, sample_prompt_workflow):
        """Test that dependencies have all required fields."""
        parser = WorkflowParser(sample_prompt_workflow)
        result = parser.parse()
        
        for dep in result['dependencies']:
            assert 'node_id' in dep
            assert 'node_type' in dep
            assert 'asset_type' in dep
            assert 'input_name' in dep
            assert 'value' in dep


# =============================================================================
# Tests for asset type mapping
# =============================================================================

class TestAssetTypeMaps:
    """Tests for asset type configuration."""
    
    def test_all_asset_types_have_required_fields(self):
        """Test that all asset types have required configuration."""
        required_fields = ['space', 'kind', 'extensions']
        
        for asset_type, config in ASSET_TYPE_MAP.items():
            for field in required_fields:
                assert field in config, f"{asset_type} missing {field}"
    
    def test_node_asset_map_format(self):
        """Test that node asset map has correct format."""
        for node_type, (asset_type, input_name) in NODE_ASSET_MAP.items():
            assert isinstance(asset_type, str)
            assert isinstance(input_name, str)
    
    def test_edge_types_defined(self):
        """Test that edge types are defined."""
        expected_types = [
            'CREATED_FROM', 'USED_MODEL', 'USED_LORA', 
            'USED_CONTROLNET', 'USED_VAE', 'USED_INPUT'
        ]
        for edge_type in expected_types:
            assert edge_type in EDGE_TYPES


# =============================================================================
# Integration tests
# =============================================================================

class TestIntegration:
    """Integration tests combining multiple components."""
    
    def test_workflow_to_lineage(self):
        """Test converting a workflow to lineage information."""
        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "flux-dev.safetensors"}
            },
            "2": {
                "class_type": "LoraLoader",
                "inputs": {
                    "model": ["1", 0],
                    "clip": ["1", 1],
                    "lora_name": "my-style.safetensors",
                    "strength_model": 1.0,
                    "strength_clip": 1.0
                }
            }
        }
        
        parser = WorkflowParser(workflow)
        result = parser.parse()
        
        # Should have both checkpoint and lora dependencies
        assert len(result['dependencies']) == 2
        
        # Build krefs from dependencies
        from kumiho_nodes.nodes import get_default_project
        project = get_default_project()
        
        for dep in result['dependencies']:
            kref = build_kref(
                project,
                ASSET_TYPE_MAP.get(dep['asset_type'], {}).get('space', dep['asset_type']),
                Path(dep['value']).stem,
                ASSET_TYPE_MAP.get(dep['asset_type'], {}).get('kind', dep['asset_type'])
            )
            assert kref.startswith('kref://')


# =============================================================================
# Tests for KumihoAssetCatalog
# =============================================================================

class TestKumihoAssetCatalog:
    """Tests for the asset catalog functionality."""
    
    def test_catalog_initialization(self):
        """Test that catalog initializes correctly."""
        from kumiho_nodes.nodes import KumihoAssetCatalog
        
        catalog = KumihoAssetCatalog()
        assert catalog._loaded == False
        assert catalog._projects == []
    
    def test_catalog_fallback_loading(self):
        """Test that catalog loads fallback data when SDK unavailable."""
        from kumiho_nodes.nodes import KumihoAssetCatalog
        
        catalog = KumihoAssetCatalog()
        catalog._load_fallback()
        
        assert catalog._loaded == True
        assert "ComfyUI@Default" in catalog._projects
        assert "ComfyUI@Default" in catalog._spaces
    
    def test_get_projects_triggers_load(self):
        """Test that get_projects triggers initial load."""
        from kumiho_nodes.nodes import KumihoAssetCatalog
        
        catalog = KumihoAssetCatalog()
        projects = catalog.get_projects()
        
        # Should have loaded something
        assert len(projects) > 0
    
    def test_get_spaces_with_project(self):
        """Test getting spaces filtered by project."""
        from kumiho_nodes.nodes import KumihoAssetCatalog
        
        catalog = KumihoAssetCatalog()
        catalog._load_fallback()
        
        spaces = catalog.get_spaces("ComfyUI@Default")
        assert len(spaces) > 0
        assert any("checkpoint" in s for s in spaces)
    
    def test_cache_key_generation(self):
        """Test cache key generation."""
        from kumiho_nodes.nodes import KumihoAssetCatalog
        
        catalog = KumihoAssetCatalog()
        key = catalog._cache_key('items', 'project', 'space')
        
        assert key == "items/project/space"


# =============================================================================
# Tests for KumihoLoadAsset input modes
# =============================================================================

class TestKumihoLoadAssetInputModes:
    """Tests for KumihoLoadAsset node with different input modes."""
    
    def test_input_types_has_project_space(self):
        """Test that INPUT_TYPES includes project and space inputs."""
        from kumiho_nodes.nodes import KumihoLoadAsset
        
        input_types = KumihoLoadAsset.INPUT_TYPES()
        
        assert "project" in input_types["optional"]
        assert "space" in input_types["optional"]
        assert "item_name" in input_types["required"]
        assert "tag / revision" in input_types["optional"]
        assert "fallback_file_path" in input_types["optional"]
    
    def test_load_asset_kref_mode(self):
        """Test load_asset with kref_uri mode."""
        from kumiho_nodes.nodes import KumihoLoadAsset
        
        node = KumihoLoadAsset()
        result, metadata_json = node.load_asset(
            item_name="model",
            item_kind="checkpoint",
            kref_uri="kref://Test/checkpoint/model.checkpoint"
        )
        
        import json
        metadata = json.loads(metadata_json)
        assert metadata['input_mode'] == 'kref_uri'
        assert metadata['effective_kref'] == "kref://Test/checkpoint/model.checkpoint"
    
    def test_load_asset_component_mode_builds_kref(self):
        """Test load_asset with component inputs builds correct kref."""
        from kumiho_nodes.nodes import KumihoLoadAsset
        
        node = KumihoLoadAsset()
        result, metadata_json = node.load_asset(
            item_name="style-lora",
            item_kind="lora",
            project="ComfyUI@Test",
            space="lora/flux"
        )
        
        import json
        metadata = json.loads(metadata_json)
        assert metadata['input_mode'] == 'components'
        assert 'lora/flux/style-lora.lora' in metadata['effective_kref']
    
    def test_load_asset_with_fallback(self):
        """Test that fallback path is used when kref fails."""
        from kumiho_nodes.nodes import KumihoLoadAsset
        
        node = KumihoLoadAsset()
        result, metadata_json = node.load_asset(
            item_name="model",
            item_kind="checkpoint",
            kref_uri="",  # Empty kref
            fallback_file_path="/models/fallback.safetensors"
        )
        
        import json
        metadata = json.loads(metadata_json)
        assert result == "/models/fallback.safetensors"
        assert metadata['status'] == 'fallback'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
