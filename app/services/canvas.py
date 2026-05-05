import json

def create_daily_canvas(notes_data: list):
    """Создает JSON для Obsidian Canvas из списка заметок"""
    nodes = []
    
    for i, (filename, category) in enumerate(notes_data):
        nodes.append({
            "id": f"node_{i}",
            "type": "file",
            "file": f"{category}/{filename}",
            "x": (i % 3) * 400,
            "y": (i // 3) * 500,
            "width": 300,
            "height": 400
        })
        
    canvas_data = {
        "nodes": nodes,
        "edges": []
    }
    
    return json.dumps(canvas_data, indent=4, ensure_ascii=False)