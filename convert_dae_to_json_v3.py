#!/usr/bin/env python3
"""
convert.py – SketchUp .dae → spatial JSON converter
Usage: python convert.py input.dae output.json

Extracts spatial metadata from Collada (.dae) files exported by SketchUp 2016.
Converts internal inch units to millimetres, resolves full 4×4 transformation
matrix hierarchies, and emits a clean JSON optimised for LLM consumption.
"""

import sys
import json
import argparse
from pathlib import Path

import numpy as np
from lxml import etree


# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------

COLLADA_NS = "http://www.collada.org/2005/11/COLLADASchema"

def _tag(local: str) -> str:
    """Return a Clark-notation tag for the COLLADA namespace."""
    return f"{{{COLLADA_NS}}}{local}"


def _find(element, local: str):
    """XPath find with COLLADA namespace."""
    return element.find(_tag(local))


def _findall(element, local: str):
    return element.findall(_tag(local))


def _findtext(element, local: str, default=""):
    el = _find(element, local)
    return el.text.strip() if el is not None and el.text else default


# ---------------------------------------------------------------------------
# 1. Unit detection
# ---------------------------------------------------------------------------

def parse_unit(root: etree._Element) -> float:
    asset = _find(root, "asset")
    if asset is None:
        return 25.4  # assume inches → mm

    unit_el = _find(asset, "unit")
    if unit_el is None:
        return 25.4

    try:
        meter = float(unit_el.get("meter", "1.0"))
    except ValueError:
        meter = 1.0

    mm_per_native = meter * 1000.0  # e.g. 0.0254 × 1000 = 25.4
    return mm_per_native


# ---------------------------------------------------------------------------
# 2. Matrix utilities
# ---------------------------------------------------------------------------

def parse_matrix(text: str) -> np.ndarray:
    values = list(map(float, text.split()))
    if len(values) != 16:
        return np.eye(4)
    return np.array(values, dtype=np.float64).reshape(4, 4)


def matrix_to_pos_rot_scale(matrix: np.ndarray) -> dict:
    # Translation is the last column
    tx, ty, tz = matrix[0, 3], matrix[1, 3], matrix[2, 3]

    # Extract scale as the length of the first three column vectors
    col0 = matrix[:3, 0]
    col1 = matrix[:3, 1]
    col2 = matrix[:3, 2]

    sx = float(np.linalg.norm(col0))
    sy = float(np.linalg.norm(col1))
    sz = float(np.linalg.norm(col2))

    # Normalise rotation columns to strip scale
    eps = 1e-9
    r = np.zeros((3, 3), dtype=np.float64)
    r[:, 0] = col0 / (sx if sx > eps else 1.0)
    r[:, 1] = col1 / (sy if sy > eps else 1.0)
    r[:, 2] = col2 / (sz if sz > eps else 1.0)

    # Euler XYZ extraction
    ry = float(np.arcsin(-np.clip(r[2, 0], -1, 1)))
    cos_ry = np.cos(ry)

    if abs(cos_ry) > eps:
        rx = float(np.arctan2(r[2, 1], r[2, 2]))
        rz = float(np.arctan2(r[1, 0], r[0, 0]))
    else:
        rx = float(np.arctan2(-r[1, 2], r[1, 1]))
        rz = 0.0

    to_deg = 180.0 / np.pi

    return {
        "position": {"x": float(tx), "y": float(ty), "z": float(tz)},
        "rotation_deg": {
            "x": round(rx * to_deg, 4),
            "y": round(ry * to_deg, 4),
            "z": round(rz * to_deg, 4),
        },
        "scale": {
            "x": round(sx, 6),
            "y": round(sy, 6),
            "z": round(sz, 6),
        },
    }


def apply_unit(trs: dict, mm_per_unit: float) -> dict:
    p = trs["position"]
    trs["position"] = {
        "x": round(p["x"] * mm_per_unit, 4),
        "y": round(p["y"] * mm_per_unit, 4),
        "z": round(p["z"] * mm_per_unit, 4),
    }
    return trs


# ---------------------------------------------------------------------------
# 3. Geometry and Node Indexing
# ---------------------------------------------------------------------------

def index_library_nodes(root: etree._Element) -> dict:
    index = {}
    lib = _find(root, "library_nodes")
    if lib is None:
        return index

    def _index_recursive(parent_el):
        for node_el in _findall(parent_el, "node"):
            nid = node_el.get("id", "")
            if nid:
                index[nid] = node_el
            _index_recursive(node_el)

    _index_recursive(lib)
    return index

def index_geometries(root: etree._Element) -> dict:
    """Map geometry ID to the geometry element."""
    index = {}
    lib = _find(root, "library_geometries")
    if lib is None:
        return index
    for geom_el in _findall(lib, "geometry"):
        gid = geom_el.get("id", "")
        if gid:
            index[gid] = geom_el
    return index

def get_geometry_vertices(root: etree._Element, geom_el: etree._Element) -> np.ndarray:
    """Extract vertex positions (flattened 3D array) from a geometry element."""
    mesh = _find(geom_el, "mesh")
    if mesh is None:
        return np.array([])
    
    # 1. Find the input with semantic="POSITION"
    vertices_el = _find(mesh, "vertices")
    if vertices_el is None:
        return np.array([])
        
    pos_input = None
    for input_el in _findall(vertices_el, "input"):
        if input_el.get("semantic") == "POSITION":
            pos_input = input_el
            break
            
    if pos_input is None:
        return np.array([])
    
    source_id = pos_input.get("source").lstrip("#")
    
    # 2. Find the source
    source_el = None
    for s in _findall(mesh, "source"):
        if s.get("id") == source_id:
            source_el = s
            break
    
    if source_el is None:
        return np.array([])
        
    # 3. Extract float array
    float_array = _find(source_el, "float_array")
    if float_array is None or float_array.text is None:
        return np.array([])
        
    values = np.fromstring(float_array.text, sep=' ')
    return values.reshape(-1, 3) # Return as N x 3 array


# ---------------------------------------------------------------------------
# 4. Recursive node traversal
# ---------------------------------------------------------------------------

def get_node_matrix(node_el: etree._Element) -> np.ndarray:
    mat_el = _find(node_el, "matrix")
    if mat_el is not None and mat_el.text:
        return parse_matrix(mat_el.text.strip())
    return np.eye(4)


def traverse_nodes(
    node_el: etree._Element,
    parent_matrix: np.ndarray,
    mm_per_unit: float,
    lib_nodes: dict,
    lib_geoms: dict,
    depth: int = 0,
) -> list:
    results = []

    name = node_el.get("name", node_el.get("id", "unnamed"))
    node_id = node_el.get("id", "")
    
    # --- Noise Filter ---
    # SketchUp 2016 specific noise
    if name.startswith("instance_") or name == "SketchUp" or name == "skp_camera":
        # Pass through to process children, but don't add to results
        local_matrix = get_node_matrix(node_el)
        world_matrix = parent_matrix @ local_matrix
        
        for child_el in _findall(node_el, "node"):
            results.extend(traverse_nodes(child_el, world_matrix, mm_per_unit, lib_nodes, lib_geoms, depth))
        
        for inst in _findall(node_el, "instance_node"):
            url = inst.get("url", "").lstrip("#")
            ref_node = lib_nodes.get(url)
            if ref_node:
                results.extend(traverse_nodes(ref_node, world_matrix, mm_per_unit, lib_nodes, lib_geoms, depth))
        
        return results

    # --- Regular Processing ---
    local_matrix = get_node_matrix(node_el)
    world_matrix = parent_matrix @ local_matrix 

    trs = matrix_to_pos_rot_scale(world_matrix)
    apply_unit(trs, mm_per_unit)
    
    # --- Collect all vertices for BB ---
    all_vertices = []

    # 1. Instance Geometry
    for inst in _findall(node_el, "instance_geometry"):
        url = inst.get("url", "").lstrip("#")
        geom_el = lib_geoms.get(url)
        if geom_el is not None:
            verts = get_geometry_vertices(None, geom_el)
            if verts.size > 0:
                # Transform vertices to world space
                # verts: N x 3. Need to pad with 1s: N x 4
                verts_h = np.hstack([verts, np.ones((verts.shape[0], 1))])
                # Multiply by world matrix (world_matrix is 4x4)
                verts_w = (world_matrix @ verts_h.T).T[:, :3]
                all_vertices.append(verts_w)

    # 2. Instance Nodes (Recursive)
    instance_children_results = []
    for inst in _findall(node_el, "instance_node"):
        url = inst.get("url", "").lstrip("#")
        ref_node = lib_nodes.get(url)
        if ref_node:
            sub = traverse_nodes(ref_node, world_matrix, mm_per_unit, lib_nodes, lib_geoms, depth + 1)
            instance_children_results.extend(sub)
    
    # Collect vertices from children too
    for child in instance_children_results:
        if "vertices" in child:
            all_vertices.append(child["vertices"])

    # --- Build record ---
    record = {
        "id": node_id,
        "name": name,
        "world_position_mm": trs["position"],
        "world_rotation_deg": trs["rotation_deg"],
        "world_scale": trs["scale"],
    }
    
    # If it has geometry, store vertices temporarily for parent BB calculation
    if all_vertices:
        combined_verts = np.vstack(all_vertices)
        record["vertices"] = combined_verts
        
        # Calculate BB
        mins = combined_verts.min(axis=0)
        maxs = combined_verts.max(axis=0)
        record["bounding_box_mm"] = {
            "min": {"x": round(mins[0]*mm_per_unit, 4), "y": round(mins[1]*mm_per_unit, 4), "z": round(mins[2]*mm_per_unit, 4)},
            "max": {"x": round(maxs[0]*mm_per_unit, 4), "y": round(maxs[1]*mm_per_unit, 4), "z": round(maxs[2]*mm_per_unit, 4)},
        }

    results.append(record)
    results.extend(instance_children_results)

    # Direct node children
    for child_el in _findall(node_el, "node"):
        results.extend(traverse_nodes(child_el, world_matrix, mm_per_unit, lib_nodes, lib_geoms, depth + 1))

    return results


# ---------------------------------------------------------------------------
# 5. Main parsing entry point
# ---------------------------------------------------------------------------

def parse_dae(path: str) -> dict:
    tree = etree.parse(path)
    root = tree.getroot()

    mm_per_unit = parse_unit(root)
    lib_nodes = index_library_nodes(root)
    lib_geoms = index_geometries(root)

    vis_scene_url = ""
    scene_el = _find(root, "scene")
    if scene_el is not None:
        ivs = _find(scene_el, "instance_visual_scene")
        if ivs is not None:
            vis_scene_url = ivs.get("url", "").lstrip("#")

    vis_scene = None
    lib_vis = _find(root, "library_visual_scenes")
    if lib_vis is not None:
        for vs in _findall(lib_vis, "visual_scene"):
            if vs.get("id", "") == vis_scene_url or vis_scene_url == "":
                vis_scene = vs
                break
    
    # Fallback
    if vis_scene is None and lib_vis is not None:
        children = _findall(lib_vis, "visual_scene")
        if children:
            vis_scene = children[0]

    nodes = []
    if vis_scene is not None:
        identity = np.eye(4)
        for top_node in _findall(vis_scene, "node"):
            nodes.extend(
                traverse_nodes(top_node, identity, mm_per_unit, lib_nodes, lib_geoms, depth=0)
            )

    # Clean up temporary vertices from final output
    for n in nodes:
        if "vertices" in n:
            del n["vertices"]

    return {
        "source_file": str(Path(path).name),
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert SketchUp .dae spatial metadata → JSON (units: mm)"
    )
    parser.add_argument("input", help="Path to the input .dae file")
    parser.add_argument("output", help="Path for the output .json file")
    args = parser.parse_args()

    input_path = Path(args.input)
    data = parse_dae(str(input_path))
    
    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)

    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()
