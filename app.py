import os
import base64
import requests
from flask import Flask, request, render_template_string
from pinecone import Pinecone
from openai import OpenAI
from dotenv import load_dotenv

# ─── 0) Load env vars ─────────────────────────────────────────────────────
load_dotenv()
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_ENV     = os.getenv("PINECONE_ENV")
PINECONE_INDEX   = os.getenv("PINECONE_INDEX")
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")

# ─── 0.5) Activation config ──────────────────────────────────────────────
EXAM_CONFIG     = {i: 'off' for i in range(1, 61)}
EXAM_CONFIG.update({1: 'on', 2: 'on', 3: 'off', 4: 'off', 5: 'off'})
SECTION_OPTIONS = ['Lectura', 'Redacción', 'Matemáticas', 'Variable']

# ─── 1) Init Pinecone & OpenAI ────────────────────────────────────────────
pc     = Pinecone(api_key=PINECONE_API_KEY, environment=PINECONE_ENV)
index  = pc.Index(PINECONE_INDEX)
client = OpenAI(api_key=OPENAI_API_KEY)
app    = Flask(__name__)

# ─── 2) HTML + MathJax setup ──────────────────────────────────────────────
HTML = '''<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Asesore Qualificato</title>
  <style>
    body{max-width:720px;margin:2rem auto;font:18px/1.4 sans-serif;color:#222;}
    h1{text-align:center;margin-bottom:1.2rem;}
    form{display:flex;flex-direction:column;gap:1rem;}
    .inline-selects{display:flex;gap:1rem;}
    textarea,select,button,input[type=file]{font-size:1rem;padding:0.6rem;}
    select{flex:1;}
    button{background:#1450b4;color:#fff;border:none;border-radius:4px;cursor:pointer;}
    button:hover{background:#0e3c86;}
    #loader{margin-top:1rem;font-style:italic;display:none;}
    .answer{margin-top:1.5rem;padding:1rem;background:#f9f9f9;border-left:4px solid #1450b4;}
    footer{margin-top:2rem;text-align:center;color:#666;font-size:0.9rem;}
  </style>
  <script>
    window.MathJax = {
      tex: { inlineMath: [['$','$'], ['\\(','\\)']], displayMath: [['$$','$$']] },
      svg: { fontCache: 'global' }
    };
  </script>
  <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js" async></script>
</head>
<body>
  <h1>Asesore Qualificato: tu tutore matemático 🤌</h1>
  <form id="qform">
    <textarea name="texto" rows="3" placeholder="Escribe tu pregunta aquí"></textarea>
    <label>— o selecciona tu pregunta:</label>
    <div class="inline-selects">
      <select name="examen">
        <option value="">Examen</option>
        {% for num, status in exam_config.items()|sort %}
          {% if status == 'on' %}
            <option value="{{ num }}">{{ num }}</option>
          {% endif %}
        {% endfor %}
      </select>
      <select name="seccion">
        <option value="">Sección</option>
        {% for opt in section_options %}
          <option value="{{ opt }}">{{ opt }}</option>
        {% endfor %}
      </select>
      <select name="pregunta">
        <option value="">Pregunta</option>
      </select>
    </div>
    <label>— o sube una imagen:</label>
    <input type="file" name="image">
    <button type="submit">Enviar</button>
  </form>

  <div id="loader">⌛ Creando la mejor respuesta</div>
  <div class="answer" id="answer"></div>
  <footer>Asesor Bebé • Demo Flask + OpenAI + Pinecone</footer>

  <script>
    const form      = document.getElementById('qform'),
          loader    = document.getElementById('loader'),
          ansDiv    = document.getElementById('answer'),
          textoEl   = form.elements['texto'],
          examenEl  = form.elements['examen'],
          seccionEl = form.elements['seccion'],
          pregEl    = form.elements['pregunta'],
          imageEl   = form.elements['image'];

    // Límites por sección
    const preguntaLimits = {
      'Lectura':      45,
      'Redacción':    25,
      'Matemáticas':  55,
      'Variable':     25
    };

    // 1) Si hay texto escrito, deshabilita selects e imagen
    textoEl.addEventListener('input', () => {
      const hasText = textoEl.value.trim().length > 0;
      [examenEl, seccionEl, pregEl, imageEl].forEach(el => {
        el.disabled = hasText;
        if (hasText) el.value = '';
      });
      seccionEl.required = false;
      pregEl.required    = false;
    });

    // 2) Si seleccionan Examen
    examenEl.addEventListener('change', () => {
      const hasExam = examenEl.value !== '';
      textoEl.disabled   = hasExam;
      imageEl.disabled   = hasExam;
      seccionEl.required = hasExam;
      pregEl.required    = hasExam;
      if (hasExam) {
        textoEl.value = '';
        imageEl.value = null;
      } else {
        seccionEl.value = '';
        pregEl.value    = '';
      }
    });

    // 3) Al cambiar Sección, repuebla Pregunta
    seccionEl.addEventListener('change', () => {
      const limit = preguntaLimits[seccionEl.value] || 0;
      pregEl.innerHTML = '<option value="">Pregunta</option>';
      for (let i = 1; i <= limit; i++) {
        const opt = document.createElement('option');
        opt.value = i;
        opt.textContent = i;
        pregEl.appendChild(opt);
      }
    });

    form.addEventListener('submit', async e => {
      e.preventDefault();
      ansDiv.innerHTML = '';

      const textoVal     = textoEl.value.trim(),
            examenVal    = examenEl.value,
            seccionVal   = seccionEl.value,
            preguntaNum  = pregEl.value,
            hasImage     = imageEl.files.length > 0,
            isTextOnly   = textoVal && !examenVal && !seccionVal && !preguntaNum && !hasImage;

      // validación cliente
      if (examenVal && (!seccionVal || !preguntaNum)) {
        ansDiv.textContent = "Cuando seleccionas examen, debes elegir sección y pregunta.";
        return;
      }

      loader.textContent = isTextOnly
        ? '⌛ Resolviendo tu pregunta'
        : '⌛ Creando la mejor respuesta';
      loader.style.display = 'block';

      let dots = 0;
      const iv = setInterval(() => {
        dots = (dots + 1) % 4;
        loader.textContent = loader.textContent.split('.')[0] + '.'.repeat(dots);
      }, 500);

      const resp = await fetch('/preguntar', {
        method: 'POST',
        body: new FormData(form)
      });

      clearInterval(iv);
      loader.style.display = 'none';

      const body = await resp.text();
      if (!resp.ok) ansDiv.textContent = body;
      else {
        ansDiv.innerHTML = body;
        MathJax.typeset();
      }
    });
  </script>
</body>
</html>'''

# ─── 3) Home route ───────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def home():
    return render_template_string(
        HTML,
        exam_config     = EXAM_CONFIG,
        section_options = SECTION_OPTIONS
    )

# ─── 4) Handle question ──────────────────────────────────────────────────
@app.route('/preguntar', methods=['POST'])
def preguntar():
    texto        = (request.form.get('texto') or "").strip()
    examen       = request.form.get('examen')
    seccion      = request.form.get('seccion')
    pregunta_num = request.form.get('pregunta')
    image_file   = request.files.get('image')

    # block mixed inputs
    if texto and (examen or seccion or pregunta_num or image_file):
        return (
            "Si escribes tu pregunta, no puedes usar “Examen”, “Sección”, "
            "“Pregunta” ni subir imagen al mismo tiempo."
        ), 400

    # require at least one input
    if not (texto or examen or seccion or pregunta_num or image_file):
        return (
            "Proporciona texto, selecciona examen/sección/pregunta o sube una imagen."
        ), 400

    # if exam-based lookup, require section & question
    if examen and not (seccion and pregunta_num):
        return "Cuando seleccionas examen, debes elegir sección y pregunta.", 400

    # 4a) Lookup snippet by metadata if exam/section/question provided
    raw_steps = []
    if examen and seccion and pregunta_num:
        try:
            pine = index.query(
                top_k=1,
                include_metadata=True,
                filter={
                    "exam":      int(examen),
                    "section":   seccion,
                    "question":  int(pregunta_num)
                }
            )
            if pine.matches:
                meta = pine.matches[0].metadata
                snippet = meta.get("text") or meta.get("answer")
                if snippet:
                    raw_steps = [snippet]
            # else fall back below
        except Exception:
            raw_steps = []

    # 4b) If no metadata match, do embedding + similarity search
    if not raw_steps:
        try:
            if image_file and not texto:
                img_bytes = image_file.read()
                emb = client.embeddings.create(
                    model='image-embedding-001',
                    input=base64.b64encode(img_bytes).decode()
                )
            else:
                emb = client.embeddings.create(
                    model='text-embedding-3-small',
                    input=texto
                )
            vector = emb.data[0].embedding
            pine = index.query(
                vector=vector,
                top_k=5,
                include_metadata=True
            )
            raw_steps = [
                m.metadata.get('text') or m.metadata.get('answer')
                for m in pine.matches
                if m.metadata.get('text') or m.metadata.get('answer')
            ]
        except Exception:
            raw_steps = []

    # 4c) Wikipedia fallback
    if not raw_steps:
        try:
            wiki = requests.get(
                'https://es.wikipedia.org/api/rest_v1/page/random/summary',
                timeout=5
            ).json()
            raw_steps = [wiki.get('extract', 'Lo siento, nada')]
        except:
            return 'No hay datos en Pinecone y falló la búsqueda aleatoria.', 500

    # 4d) HTML formatting via LLM
    format_msg = (
        'Eres un formateador HTML muy estricto. Toma estas frases y devuélvelas '
        'como una lista ordenada (<ol><li>…</li></ol>) en español, sin texto '
        'adicional. Usa siempre los delimitadores LaTeX \\(…\\) para las fórmulas.\n\n'
        + '\n'.join(f'- {s}' for s in raw_steps)
    )
    try:
        chat = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role':'system','content':format_msg},
                {'role':'user',  'content':'Por favor formatea la lista.'}
            ]
        )
        formatted_list = chat.choices[0].message.content.strip()
    except Exception as e:
        return f'Error de formateo: {e}', 500

    # 4e) Build and return response
    response_fragment = (
        f"<p><strong>Enunciado:</strong> {texto}</p>"
        f"<p><strong>Examen:</strong> {examen}</p>"
        f"<p><strong>Sección:</strong> {seccion}</p>"
        f"<p><strong>Pregunta nº:</strong> {pregunta_num}</p>"
        f"{formatted_list} 🤌"
    )
    return response_fragment

# ─── 5) Run server ───────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT','8000')),
        debug=False
    )
