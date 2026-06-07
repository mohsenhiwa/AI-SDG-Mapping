import os
import re
import json
import logging
from flask import Flask, render_template, request, jsonify, Response
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from google import genai
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)

load_dotenv()

app = Flask(__name__)
ai_client = genai.Client()

CACHE_FILE_PATH = "cached_audit_matrix.json"
CORE_COLUMNS = ["code", "title", "credits", "semester"]

SDG_KEYWORDS = {
    "SDG 1 - No poverty": ["poverty", "income distributio", "wealth distributio", "socio-economic"],
    "SDG 2 - Zero hunger": ["agriculture", "food", "insecurity", "nutrition"],
    "SDG 3 - Good health and well-being": ["health", "well-being"],
    "SDG 4 - Quality education": ["educat", "inclusive", "equitable"],
    "SDG 5 - Gender equality": ["gender", "women", "equality", "inequality", "girl", "queer"],
    "SDG 6 - Clean water and sanitation": ["water", "sanitation"],
    "SDG 7 - Affordable and clean energy": ["energy", "renewable", "wind", "solar", "geothermal", "hydroelectric"],
    "SDG 8 - Decent work and economic growth": ["employment", "econom", "economic growth", "sustainable developme", "labour", "worker", "circular econom", "wage"],
    "SDG 9 - Industry, innovation and infrastructure": ["infrastructur", "innovation", "industr", "buildings"],
    "SDG 10 - Reduced inequalities": ["trade", "inequality", "financial market", "taxation"],
    "SDG 11 - Sustainable cities and communities": ["cities", "urban", "resilien", "rural"],
    "SDG 12 - Responsible consumption and production": ["consum", "production", "waste", "natural resource", "recycl", "industrial ecology", "sustainabl e design"],
    "SDG 13 - Climate action": ["climat", "greenhouse", "greenhouse gas", "environmen", "global warming", "carbon", "weather", "climate crisis"],
    "SDG 14 - Life below water": ["ocean", "marine", "water", "pollut", "conserv", "fish"],
    "SDG 15 - Life on land": ["forest", "biodiversit", "ecology", "pollut", "conserv", "land use"],
    "SDG 16 - Peace, justice and strong institutions": ["justice", "governanc", "peace", "rights"],
    "SDG 17 - Partnerships for the goals": ["partnership", "collaboration", "global cooperation", "stakeholder", "resource mobilization"]
}

SECTION_WEIGHTS = {
    "learning_outcomes": 1.0,
    "syllabus": 0.8,
    "objectives": 0.5,
    "module_summary": 0.2,
    "programme_specification": 0.1
}

def parse_section_text(soup, heading_text):
    heading = soup.find(lambda tag: tag.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong'] 
                        and heading_text.lower() in tag.get_text().lower())
    if not heading: return ""
    content_pieces = []
    current_element = heading.next_sibling
    while current_element:
        if current_element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']: break
        if current_element.name in ['p', 'ul', 'ol', 'div', 'span']:
            txt = current_element.get_text(separator=' ', strip=True)
            if txt: content_pieces.append(txt)
        elif current_element.name is None:  
            raw_str = current_element.strip()
            if raw_str: content_pieces.append(raw_str)
        current_element = current_element.next_sibling
    return "\n".join(content_pieces) if content_pieces else ""


def score_module_deterministically(module_record, prog_spec_text):
    scores_payload = {}
    for sdg, terms in SDG_KEYWORDS.items():
        total_weighted_hits = 0.0
        evidence_segments = []
        for section, weight in SECTION_WEIGHTS.items():
            text_context = prog_spec_text if section == "programme_specification" else module_record.get(section, "")
            if not text_context: continue
            section_hits = sum(len(re.findall(r'\b' + re.escape(term), text_context, re.IGNORECASE)) for term in terms)
            if section_hits > 0:
                total_weighted_hits += (section_hits * weight)
                evidence_segments.append(f"{section.replace('_', ' ').title()}: {section_hits} hits")
                
        if total_weighted_hits == 0:
            score, classification = 0, "None"
        elif total_weighted_hits <= 2.2:
            score, classification = 1, "Some (Implicit)"
        else:
            score, classification = 2, "Significant (Explicit)"
            
        scores_payload[sdg] = {
            "score": score,
            "classification": classification,
            "evidence_math_summary": ", ".join(evidence_segments) if evidence_segments else "No keywords matched"
        }
    return scores_payload


def crawl_single_module(mod_code, mod_title, mod_credits, mod_semester, module_absolute_url, headers, prog_spec_text):
    module_record = {
        "code": mod_code, "title": mod_title, "credits": mod_credits, "semester": mod_semester,
        "module_url": module_absolute_url, "module_summary": "", "objectives": "", "learning_outcomes": "", "syllabus": ""
    }
    try:
        mod_resp = requests.get(module_absolute_url, timeout=6, headers=headers)
        if mod_resp.status_code == 200:
            mod_soup = BeautifulSoup(mod_resp.text, 'html.parser')
            module_record["module_summary"] = parse_section_text(mod_soup, "Module Summary")
            module_record["objectives"] = parse_section_text(mod_soup, "Objectives")
            module_record["learning_outcomes"] = parse_section_text(mod_soup, "Learning Outcomes")
            module_record["syllabus"] = parse_section_text(mod_soup, "Syllabus")
    except Exception as e:
        logging.error(f"Error crawling module link {mod_code}: {e}")
        
    module_record["sdg_scores"] = score_module_deterministically(module_record, prog_spec_text)
    return module_record


def extract_and_score_curriculum(programme_url):
    headers = {'User-Agent': 'PrecisionLeedsHybridEngineCrawler/4.0'}
    try:
        response = requests.get(programme_url, timeout=12, headers=headers)
        response.raise_for_status()
    except Exception as e: return None, f"Network fault on base URL: {str(e)}"
        
    soup = BeautifulSoup(response.text, 'html.parser')
    prog_spec_heading = soup.find(lambda tag: tag.name in ['h1', 'h2', 'h3', 'h4'] and "programme specification" in tag.get_text().lower())
    prog_spec_content = ""
    if prog_spec_heading:
        curr = prog_spec_heading.next_sibling
        while curr:
            if curr.name in ['h1', 'h2', 'h3', 'table'] or (curr.name == 'div' and 'table-responsive' in curr.get('class', [])): break
            if curr.get_text:
                txt = curr.get_text(strip=True)
                if txt: prog_spec_content += txt + "\n"
            curr = curr.next_sibling
    else:
        intro_box = soup.find('div', class_='programme-description')
        prog_spec_content = intro_box.get_text(separator='\n', strip=True) if intro_box else soup.get_text(separator=' ', strip=True)[:1500]

    page_title = soup.find('h1').get_text(strip=True) if soup.find('h1') else "Academic Programme Specification"
    prog_spec_cleaned = prog_spec_content.strip()
    
    data_payload = {
        "programme": {"title": page_title, "programme_specification": prog_spec_cleaned, "programme_sdg_scores": {}},
        "modules": []
    }
    
    all_tables = soup.find_all('table')
    visited_module_codes = set()
    crawl_tasks = []
    
    for table in all_tables:
        header_row = table.find('tr') or table.find('thead')
        if not header_row: continue
        columns = [th.get_text(strip=True).lower() for th in header_row.find_all(['th', 'td'])]
        if not all(col in columns for col in CORE_COLUMNS): continue
        col_indices = {col: columns.index(col) for col in CORE_COLUMNS}
        
        for row in table.find_all('tr')[1:]:
            cells = row.find_all(['td', 'th'])
            if len(cells) < len(columns): continue
            mod_code = cells[col_indices["code"]].get_text(strip=True)
            mod_title = cells[col_indices["title"]].get_text(strip=True)
            mod_credits = cells[col_indices["credits"]].get_text(strip=True)
            mod_semester = cells[col_indices["semester"]].get_text(strip=True)
            
            if not mod_code or len(mod_code) > 15 or mod_code in visited_module_codes: continue 
            link_element = cells[col_indices["code"]].find('a', href=True) or cells[col_indices["title"]].find('a', href=True)
            if not link_element: continue
            
            module_absolute_url = urljoin(programme_url, link_element['href'])
            visited_module_codes.add(mod_code)
            crawl_tasks.append((mod_code, mod_title, mod_credits, mod_semester, module_absolute_url))

    if not crawl_tasks: return None, "No verified criteria layout tables found."

    with ThreadPoolExecutor(max_workers=min(10, len(crawl_tasks))) as executor:
        futures = {executor.submit(crawl_single_module, c, t, cr, s, u, headers, prog_spec_cleaned): c for c, t, cr, s, u in crawl_tasks}
        for future in as_completed(futures):
            data_payload["modules"].append(future.result())

    data_payload["modules"].sort(key=lambda x: x["code"])

    for sdg in SDG_KEYWORDS.keys():
        mod_scores = [m["sdg_scores"][sdg]["score"] for m in data_payload["modules"]]
        sig_count = sum(1 for s in mod_scores if s == 2)
        som_count = sum(1 for s in mod_scores if s == 1)
        prog_score = 2 if sig_count >= 2 else (1 if sig_count >= 1 or som_count >= 2 else 0)
        data_payload["programme"]["programme_sdg_scores"][sdg] = {"score": prog_score}

    return data_payload, None


@app.route('/')
def index():
    return render_template('Mapping.html')


@app.route('/api/scan', methods=['POST'])
def scan_website():
    data = request.get_json()
    target_url = data.get('url')
    if not target_url: return jsonify({"error": "No URL provided"}), 400

    structured_json, crawl_error = extract_and_score_curriculum(target_url)
    if crawl_error: return jsonify({"error": crawl_error}), 400

    try:
        with open(CACHE_FILE_PATH, "w", encoding="utf-8") as cache_file:
            json.dump(structured_json, cache_file, indent=2, ensure_ascii=False)
    except Exception as cache_err:
        logging.error(f"Failed to write disk cache file structure: {cache_err}")

    # Synchronized Consistency: Returns raw structured data directly inside the API payload 
    return jsonify({
        "success": True,
        "programme_title": structured_json["programme"]["title"],
        "modules": [{"code": m["code"], "title": m["title"], "scores": {k: v["score"] for k, v in m["sdg_scores"].items()}} for m in structured_json["modules"]],
        "programme_scores": {k: v["score"] for k, v in structured_json["programme"]["programme_sdg_scores"].items()},
        "raw_payload": structured_json
    })


@app.route('/api/chat', methods=['POST'])
def chat_module():
    data = request.get_json()
    selected_module_code = data.get('module_code')
    user_message = data.get('message')
    
    if not os.path.exists(CACHE_FILE_PATH):
        return jsonify({"reply": "Cache store file missing. Please click 'Run Hybrid Audit' to extract the curriculum dataset."}), 400

    try:
        with open(CACHE_FILE_PATH, "r", encoding="utf-8") as cache_file:
            cached_audit = json.load(cache_file)
    except Exception as read_err:
        return jsonify({"reply": f"Failed to open server-side data files: {str(read_err)}"}), 500

    module_data = next((m for m in cached_audit["modules"] if m["code"] == selected_module_code), None)
    if not module_data:
        return jsonify({"reply": f"Could not find a mapped data record for module {selected_module_code}."}), 400

    prompt = (
        "You are an academic curriculum mapping specialist answering questions about a degree syllabus.\n"
        "A deterministic Python evidence engine has already scanned this specific module and generated its "
        "sustainability scores (0 = None, 1 = Some/Implicit, 2 = Significant/Explicit).\n\n"
        f"--- AUDITED MODULE DATA PACKET ({module_data['code']}) ---\n"
        f"Title: {module_data['title']}\n"
        f"Credits: {module_data['credits']} | Semester: {module_data['semester']}\n"
        f"Summary Field: {module_data['module_summary']}\n"
        f"Objectives Field: {module_data['objectives']}\n"
        f"Learning Outcomes Field: {module_data['learning_outcomes']}\n"
        f"Syllabus Field: {module_data['syllabus']}\n\n"
        f"Calculated Algorithmic SDG Scores for this module:\n{json.dumps(module_data['sdg_scores'], indent=2)}\n\n"
        "Instructions:\n"
        "- Base your response directly on the provided data packet text fields.\n"
        "- Explain exactly why the module received its calculated score based on your evidence hierarchy (Learning Outcomes hold the highest weight).\n"
        "- Provide actionable ideas on how to update the module's syllabus or learning outcomes if the user asks how to improve its score.\n\n"
        f"User Question: {user_message}"
    )

    try:
        response = ai_client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return jsonify({"reply": response.text})
    except Exception as e:
        return jsonify({"reply": f"AI conversational loop error: {str(e)}"}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)