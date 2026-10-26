from flask import Flask, render_template, request, send_from_directory, redirect, url_for, Response
import os
import json
import math
import re
from datetime import datetime, timedelta

app = Flask(__name__)

RECORD_ROOT = os.path.expanduser('/mnt/megadisk/dowloads_geral/record')
ALIASES_FILE = 'camera_aliases.json'

def load_aliases():
    if os.path.exists(ALIASES_FILE):
        with open(ALIASES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_aliases(aliases):
    with open(ALIASES_FILE, 'w', encoding='utf-8') as f:
        json.dump(aliases, f, ensure_ascii=False, indent=4)

def parse_filename(filename):
    """Converte '20261013_220858_00.mp4' para '13/10/2026 22:08:58'"""
    try:
        name_wo_ext = os.path.splitext(filename)[0]
        parts = name_wo_ext.split('_')
        if len(parts) >= 2:
            date_str = parts[0]
            time_str = parts[1]
            ano, mes, dia = date_str[0:4], date_str[4:6], date_str[6:8]
            hora, minuto, segundo = time_str[0:2], time_str[2:4], time_str[4:6]
            return f"{dia}/{mes}/{ano} - {hora}:{minuto}:{segundo}"
    except Exception:
        pass
    return filename

def limpar_gravacoes_antigas(dias_limite=5):
    """Apaga arquivos .mp4 com mais de X dias com base no nome do arquivo (YYYYMMDD)"""
    if not os.path.exists(RECORD_ROOT):
        return

    data_atual = datetime.now()
    limite_data = data_atual - timedelta(days=dias_limite)
    
    removidos = 0
    for folder_name in os.listdir(RECORD_ROOT):
        folder_path = os.path.join(RECORD_ROOT, folder_name)
        if os.path.isdir(folder_path):
            for file in os.listdir(folder_path):
                if file.endswith('.mp4'):
                    try:
                        date_str = file.split('_')[0]
                        file_date = datetime.strptime(date_str, '%Y%m%d')
                        if file_date < limite_data:
                            file_path = os.path.join(folder_path, file)
                            os.remove(file_path)
                            removidos += 1
                    except Exception:
                        continue
    if removidos > 0:
        print(f"Limpeza automática: {removidos} gravações com mais de {dias_limite} dias foram apagadas.")

@app.route('/')
def index():
    aliases = load_aliases()
    cameras = []
    
    if os.path.exists(RECORD_ROOT):
        for folder_name in os.listdir(RECORD_ROOT):
            folder_path = os.path.join(RECORD_ROOT, folder_name)
            if os.path.isdir(folder_path):
                friendly_name = aliases.get(folder_name, folder_name)
                cameras.append({
                    'original_name': folder_name,
                    'friendly_name': friendly_name
                })
                
    return render_template('index.html', cameras=cameras)

@app.route('/camera/<folder_name>')
def view_camera(folder_name):
    aliases = load_aliases()
    friendly_folder = aliases.get(folder_name, folder_name)
    folder_path = os.path.join(RECORD_ROOT, folder_name)
    
    videos = []
    if os.path.exists(folder_path):
        for file in sorted(os.listdir(folder_path), reverse=True):
            if file.endswith('.mp4'):
                videos.append({
                    'filename': file,
                    'datetime': parse_filename(file)
                })
                
    page = request.args.get('page', 1, type=int)
    per_page = 12
    total_videos = len(videos)
    total_pages = math.ceil(total_videos / per_page) if total_videos > 0 else 1
    
    page = max(1, min(page, total_pages))
    
    start = (page - 1) * per_page
    end = start + per_page
    paginated_videos = videos[start:end]
                
    return render_template(
        'camera.html', 
        folder_name=folder_name, 
        friendly_folder=friendly_folder, 
        videos=paginated_videos,
        page=page,
        total_pages=total_pages
    )

@app.route('/video/<folder_name>/<filename>')
def stream_video(folder_name, filename):
    folder_path = os.path.join(RECORD_ROOT, folder_name)
    file_path = os.path.join(folder_path, filename)
    
    if not os.path.exists(file_path):
        return "Vídeo não encontrado", 404

    file_size = os.path.getsize(file_path)
    range_header = request.headers.get('Range', None)

    if not range_header:
        return send_from_directory(folder_path, filename)

    byte_range = re.search(r'bytes=(\d+)-(\d*)', range_header)
    if not byte_range:
        return send_from_directory(folder_path, filename)

    start = int(byte_range.group(1))
    end = int(byte_range.group(2)) if byte_range.group(2) else file_size - 1
    
    if end >= file_size:
        end = file_size - 1

    length = end - start + 1

    def generate():
        with open(file_path, 'rb') as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk_size = min(4096, remaining)
                data = f.read(chunk_size)
                if not data:
                    break
                remaining -= len(data)
                yield data

    rv = Response(generate(), 206, mimetype='video/mp4', direct_passthrough=True)
    rv.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
    rv.headers.add('Accept-Ranges', 'bytes')
    rv.headers.add('Content-Length', str(length))
    return rv

@app.route('/rename_folder', methods=['POST'])
def rename_folder():
    folder_name = request.form.get('folder_name')
    new_friendly = request.form.get('new_name')
    aliases = load_aliases()
    aliases[folder_name] = new_friendly
    save_aliases(aliases)
    return redirect(url_for('index'))

if __name__ == '__main__':
    limpar_gravacoes_antigas(dias_limite=5)
    app.run(debug=True, port=5000)