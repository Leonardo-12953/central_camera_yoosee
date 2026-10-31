
from flask import Flask, render_template, request, send_from_directory, redirect, url_for, Response
import os
import json
import math
import re
from datetime import datetime, timedelta

app = Flask(__name__)

CONFIG_FILE = "config.json"


def load_config():

    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    return {
        "system": {
            "record_root": "",
            "retention_days": 5
        },
        "cameras": {}
    }


def save_config(config):

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def get_system_config():
    return load_config()["system"]


def get_cameras():
    return load_config()["cameras"]


def parse_filename(filename):
    """Converte YYYYMMDD_HHMMSS para DD/MM/YYYY - HH:MM:SS"""

    try:
        name_wo_ext = os.path.splitext(filename)[0]
        parts = name_wo_ext.split('_')

        if len(parts) >= 2:
            date_str = parts[0]
            time_str = parts[1]

            ano = date_str[0:4]
            mes = date_str[4:6]
            dia = date_str[6:8]

            hora = time_str[0:2]
            minuto = time_str[2:4]
            segundo = time_str[4:6]

            return f"{dia}/{mes}/{ano} - {hora}:{minuto}:{segundo}"

    except Exception:
        pass

    return filename

def limpar_gravacoes_antigas():

    system = get_system_config()

    record_root = system["record_root"]
    retention_days = system["retention_days"]

    if not record_root or not os.path.exists(record_root):
        return

    data_atual = datetime.now()
    limite_data = data_atual - timedelta(days=retention_days)

    removidos = 0

    for folder_name in os.listdir(record_root):

        folder_path = os.path.join(record_root, folder_name)

        if os.path.isdir(folder_path):

            for file in os.listdir(folder_path):

                if file.endswith('.mp4'):

                    try:
                        date_str = file.split('_')[0]

                        file_date = datetime.strptime(
                            date_str, '%Y%m%d'
                        )

                        if file_date < limite_data:

                            file_path = os.path.join(
                                folder_path, file
                            )

                            os.remove(file_path)
                            removidos += 1

                    except Exception:
                        continue

    if removidos > 0:
        print(
            f"Limpeza automática: {removidos} gravações "
            f"com mais de {retention_days} dias foram apagadas."
        )


@app.route('/')
def index():

    system = get_system_config()
    aliases = get_cameras()

    record_root = system["record_root"]

    cameras = []

    if record_root and os.path.exists(record_root):

        for folder_name in os.listdir(record_root):

            folder_path = os.path.join(
                record_root, folder_name
            )

            if os.path.isdir(folder_path):

                friendly_name = aliases.get(
                    folder_name, folder_name
                )

                cameras.append({
                    'original_name': folder_name,
                    'friendly_name': friendly_name
                })

    return render_template(
        'index.html',
        cameras=cameras
    )

@app.route('/camera/<folder_name>')
def view_camera(folder_name):

    system = get_system_config()
    aliases = get_cameras()

    record_root = system["record_root"]

    friendly_folder = aliases.get(
        folder_name, folder_name
    )

    folder_path = os.path.join(
        record_root, folder_name
    )

    videos = []

    if os.path.exists(folder_path):

        for file in sorted(
            os.listdir(folder_path), reverse=True
        ):

            if file.endswith('.mp4'):

                videos.append({
                    'filename': file,
                    'datetime': parse_filename(file)
                })

    search = request.args.get(
        'search', ''
    ).lower()

    if search:

        videos = [
            v for v in videos
            if search in v['filename'].lower()
            or search in v['datetime'].lower()
        ]

    page = request.args.get(
        'page', 1, type=int
    )

    per_page = 12

    total_videos = len(videos)

    total_pages = (
        math.ceil(total_videos / per_page)
        if total_videos > 0 else 1
    )

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
        total_pages=total_pages,
        search=search
    )

@app.route('/video/<folder_name>/<filename>')
def stream_video(folder_name, filename):

    system = get_system_config()

    record_root = system["record_root"]

    folder_path = os.path.join(
        record_root, folder_name
    )

    file_path = os.path.join(
        folder_path, filename
    )

    if not os.path.exists(file_path):
        return "Vídeo não encontrado", 404

    file_size = os.path.getsize(file_path)

    range_header = request.headers.get(
        'Range', None
    )

    if not range_header:
        return send_from_directory(
            folder_path, filename
        )

    byte_range = re.search(
        r'bytes=(\d+)-(\d*)', range_header
    )

    if not byte_range:
        return send_from_directory(
            folder_path, filename
        )

    start = int(byte_range.group(1))

    end = (
        int(byte_range.group(2))
        if byte_range.group(2)
        else file_size - 1
    )

    if start >= file_size:
        return Response(status=416)

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

    rv = Response(
        generate(),
        206,
        mimetype='video/mp4',
        direct_passthrough=True
    )

    rv.headers.add(
        'Content-Range',
        f'bytes {start}-{end}/{file_size}'
    )

    rv.headers.add(
        'Accept-Ranges', 'bytes'
    )

    rv.headers.add(
        'Content-Length', str(length)
    )

    return rv

@app.route('/config', methods=['GET', 'POST'])
def config_page():

    config = load_config()

    if request.method == 'POST':

        config["system"]["record_root"] = (
            request.form.get("record_root", "").strip()
        )

        config["system"]["retention_days"] = int(
            request.form.get("retention_days", 5)
        )

        save_config(config)

        return redirect(url_for("config_page"))

    return render_template(
        "config.html",
        config=config["system"]
    )


@app.route('/rename_folder', methods=['POST'])
def rename_folder():

    folder_name = request.form.get('folder_name')
    new_friendly = request.form.get('new_name')

    config = load_config()

    config["cameras"][folder_name] = new_friendly

    save_config(config)

    return redirect(url_for('index'))


if __name__ == '__main__':

    limpar_gravacoes_antigas()

    app.run(
        debug=True,
        port=5000
    )
