import os
import re
import json
import logging
from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)

app = Flask(__name__)

CORE_COLUMNS = ["code", "title", "credits", "semester"]

FALSE_POSITIVE_EXCLUSIONS = [
    r'\bvirtual learning environment\b',
    r'\blearning environment\b',
    r'\bteaching environment\b',
    r'\bacademic environment\b',
    r'\bbusiness environment\b',
    r'\bcommercial environment\b',
    r'\bcorporate environment\b',
    r'\bprogramming environment\b',
    r'\bdevelopment environment\b',
    r'\bsoftware environment\b',
    r'\bcomputing environment\b',
    r'\bcloud environment\b',
    r'\bsimulation environment\b',
    r'\bregulatory environment\b',
    r'\bhigher education\b',
    r'\bfurther education\b',
    r'\bsecondary education\b',
    r'\bprimary education\b',
    r'\bmedical education\b',
    r'\bclinical education\b',
    r'\bengineering education\b',
    r'\bwatercolour\b',
    r'\bwatercolor\b',
    r'\bwaterfall\b',
]

SDG_KEYWORDS = {
    "SDG 1 - No poverty": {
        "explicit": [
            "poverty", "extreme poverty", "multidimensional poverty", "alleviation of poverty",
            "poverty reduction", "income distribution", "wealth distribution", "social protection",
            "universal basic income", "living wage", "microfinance", "financial inclusion",
            "homelessness", "material deprivation", "fuel poverty", "working poor",
            "welfare state", "social safety net", "economic vulnerability"
        ],
        "supportive": [
            "socio-economic", "socioeconomic", "deprivation", "disadvantaged",
            "low-income", "low income", "redistribution", "welfare"
        ]
    },
    "SDG 2 - Zero hunger": {
        "explicit": [
            "zero hunger", "food security", "food insecurity", "malnutrition", "undernourishment",
            "sustainable agriculture", "agroecology", "food system", "food sovereignty",
            "crop yield", "soil fertility", "regenerative agriculture", "organic farming",
            "food supply chain", "famine", "micronutrient", "sustainable food",
            "agrobiodiversity", "precision agriculture", "smallholder farmer"
        ],
        "supportive": [
            "agriculture", "agricultural", "nutrition", "nutritional", "food",
            "crop", "livestock", "farming", "agronomy", "horticulture"
        ]
    },
    "SDG 3 - Good health and well-being": {
        "explicit": [
            "public health", "global health", "mental health", "well-being", "wellbeing",
            "epidemiolog", "mortality", "morbidity", "non-communicable disease",
            "infectious disease", "pandemic", "vaccine", "immunisation", "immunization",
            "universal health coverage", "healthcare access", "maternal health",
            "child health", "occupational health", "environmental health", "health inequality",
            "health inequalities", "addiction", "substance abuse", "air pollution health"
        ],
        "supportive": [
            "health", "healthcare", "clinical", "disease", "patient",
            "pharmacolog", "therap", "psycholog", "well-being"
        ]
    },
    "SDG 4 - Quality education": {
        "explicit": [
            "inclusive education", "equitable education", "education for sustainable development",
            "lifelong learning", "widening participation", "educational inequality",
            "educational inequalities", "digital literacy", "adult literacy",
            "early childhood development", "universal access to education",
            "global citizenship education", "pedagogical equity", "special educational needs"
        ],
        "supportive": [
            "inclusive", "equitable", "literacy", "pedagog", "curriculum",
            "access to education", "social mobility"
        ]
    },
    "SDG 5 - Gender equality": {
        "explicit": [
            "gender equality", "gender inequality", "gender inequalities", "gender equity",
            "women's rights", "female empowerment", "feminis", "gender-based violence",
            "domestic violence", "gender pay gap", "reproductive rights", "reproductive health",
            "intersectionality", "intersectional", "patriarch", "queer", "lgbtq",
            "gender mainstreaming", "women in leadership", "unpaid care"
        ],
        "supportive": [
            "gender", "women", "girl", "femininity", "masculinity", "sexism"
        ]
    },
    "SDG 6 - Clean water and sanitation": {
        "explicit": [
            "clean water", "drinking water", "water quality", "water scarcity",
            "water security", "water supply", "wastewater", "sewage", "sanitation",
            "hygiene", "water treatment", "desalination", "water resource management",
            "catchment management", "groundwater", "aquifer", "water pollution",
            "hydrolog", "flood risk", "flood management", "water governance"
        ],
        "supportive": [
            "water", "hydraulics", "drainage", "river", "freshwater", "irrigation"
        ]
    },
    "SDG 7 - Affordable and clean energy": {
        "explicit": [
            "clean energy", "renewable energy", "affordable energy", "energy transition",
            "decarbonisation of energy", "decarbonization of energy", "solar energy",
            "solar power", "wind energy", "wind power", "offshore wind", "geothermal",
            "hydroelectric", "hydropower", "bioenergy", "biofuel", "hydrogen economy",
            "green hydrogen", "energy efficiency", "energy storage", "battery storage",
            "smart grid", "energy poverty", "fossil fuel phase-out", "low-carbon energy"
        ],
        "supportive": [
            "energy", "renewable", "solar", "wind", "grid", "photovoltaic", "turbine", "nuclear"
        ]
    },
    "SDG 8 - Decent work and economic growth": {
        "explicit": [
            "decent work", "economic growth", "sustainable economic", "circular economy",
            "green economy", "inclusive growth", "labour rights", "labor rights",
            "worker rights", "modern slavery", "forced labour", "child labour",
            "youth employment", "unemployment", "precarious work", "gig economy",
            "occupational safety", "minimum wage", "living wage", "fair trade",
            "social enterprise", "sustainable tourism"
        ],
        "supportive": [
            "employment", "labour", "worker", "wage", "econom", "entrepreneur",
            "productivity", "macroeconom", "microeconom"
        ]
    },
    "SDG 9 - Industry, innovation and infrastructure": {
        "explicit": [
            "sustainable infrastructure", "resilient infrastructure", "green innovation",
            "clean technology", "cleantech", "industrial decarbonisation",
            "industrial decarbonization", "sustainable industrialisation",
            "sustainable industrialization", "resource-use efficiency",
            "retrofitting", "digital divide", "broadband access", "research and development",
            "technological innovation", "low-carbon infrastructure", "smart infrastructure"
        ],
        "supportive": [
            "infrastructur", "innovation", "industr", "buildings", "manufacturing",
            "automation", "telecommunications", "transport network"
        ]
    },
    "SDG 10 - Reduced inequalities": {
        "explicit": [
            "reduced inequalities", "reducing inequality", "social inequality",
            "social inequalities", "economic inequality", "income inequality",
            "wealth gap", "social inclusion", "marginalised", "marginalized",
            "anti-discrimination", "racial justice", "racism", "decoloni",
            "global south", "migration policy", "refugee", "asylum",
            "disability rights", "accessibility", "progressive taxation", "fair trade"
        ],
        "supportive": [
            "inequality", "inequalities", "equity", "equality", "discrimination",
            "migration", "taxation", "financial market", "trade", "minority"
        ]
    },
    "SDG 11 - Sustainable cities and communities": {
        "explicit": [
            "sustainable cities", "sustainable communities", "urban sustainability",
            "urban resilience", "urban planning", "affordable housing", "social housing",
            "public transport", "active travel", "sustainable transport", "green space",
            "green spaces", "urban heat island", "air quality", "cultural heritage",
            "natural heritage", "disaster risk reduction", "urban regeneration",
            "smart cities", "slum", "informal settlement", "peri-urban"
        ],
        "supportive": [
            "cities", "urban", "rural", "housing", "municipal", "resilien",
            "built environment", "transport", "spatial planning"
        ]
    },
    "SDG 12 - Responsible consumption and production": {
        "explicit": [
            "responsible consumption", "sustainable consumption", "sustainable production",
            "circular economy", "life cycle assessment", "lifecycle assessment",
            "life-cycle analysis", "waste management", "waste reduction", "zero waste",
            "recycling", "remanufacturing", "industrial ecology", "sustainable design",
            "ecodesign", "eco-design", "sustainable procurement", "sustainable supply chain",
            "hazardous waste", "e-waste", "plastic pollution", "food waste",
            "corporate social responsibility", "sustainability reporting", "esg"
        ],
        "supportive": [
            "consum", "production", "waste", "natural resource", "recycl",
            "supply chain", "packaging", "material efficiency"
        ]
    },
    "SDG 13 - Climate action": {
        "explicit": [
            "climate change", "climate action", "climate crisis", "climate emergency",
            "climate mitigation", "climate adaptation", "global warming",
            "greenhouse gas", "carbon footprint", "carbon capture", "carbon neutral",
            "net zero", "net-zero", "decarbonis", "decarboniz", "paris agreement",
            "ipcc", "emissions trading", "carbon tax", "climate justice",
            "climate resilience", "extreme weather", "sea level rise", "carbon sink"
        ],
        "supportive": [
            "climat", "greenhouse", "environmen", "carbon", "emission",
            "weather", "fossil fuel", "sustainability"
        ]
    },
    "SDG 14 - Life below water": {
        "explicit": [
            "life below water", "marine", "ocean", "oceans", "marine ecosystem",
            "marine biodiversity", "marine pollution", "ocean acidification",
            "overfishing", "sustainable fisheries", "fisheries", "aquaculture",
            "coral reef", "coastal erosion", "coastal management", "microplastic",
            "plastic debris", "eutrophication", "deep sea", "maritime conservation"
        ],
        "supportive": [
            "coastal", "aquatic", "estuar", "fish", "sea", "maritime"
        ]
    },
    "SDG 15 - Life on land": {
        "explicit": [
            "life on land", "biodiversity", "ecosystem restoration", "deforestation",
            "reforestation", "afforestation", "soil degradation", "soil erosion",
            "desertification", "wildlife conservation", "habitat loss",
            "endangered species", "invasive species", "terrestrial ecosystem",
            "rewilding", "nature-based solution", "nature-based solutions",
            "forest management", "peatland", "wetland", "land degradation"
        ],
        "supportive": [
            "forest", "ecology", "ecological", "land use", "conservation",
            "soil", "habitat", "flora", "fauna"
        ]
    },
    "SDG 16 - Peace, justice and strong institutions": {
        "explicit": [
            "peace and justice", "strong institutions", "rule of law", "human rights",
            "anti-corruption", "corruption", "bribery", "democratic governance",
            "accountable institutions", "transparency", "access to justice",
            "conflict resolution", "peacebuilding", "armed conflict", "war crimes",
            "freedom of expression", "press freedom", "civic space", "civil liberties",
            "inclusive decision-making", "transitional justice"
        ],
        "supportive": [
            "justice", "governanc", "peace", "rights", "democra",
            "legislation", "public policy", "accountability", "ethic"
        ]
    },
    "SDG 17 - Partnerships for the goals": {
        "explicit": [
            "partnerships for the goals", "global partnership", "multi-stakeholder",
            "multistakeholder", "international cooperation", "global cooperation",
            "development assistance", "official development assistance",
            "technology transfer", "capacity building", "capacity-building",
            "debt sustainability", "sustainable finance", "north-south cooperation",
            "south-south cooperation", "policy coherence", "public-private partnership"
        ],
        "supportive": [
            "partnership", "collaboration", "stakeholder", "resource mobilization",
            "international development", "global governance"
        ]
    }
}

SECTION_WEIGHTS = {
    "learning_outcomes": 1.0,
    "syllabus": 0.8,
    "objectives": 0.5,
    "module_summary": 0.2,
    "programme_specification": 0.1
}

TIER_MULTIPLIERS = {
    "explicit": 1.0,
    "supportive": 0.45
}


def clean_academic_boilerplate(text):
    if not text:
        return ""
    cleaned = text
    for pattern in FALSE_POSITIVE_EXCLUSIONS:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
    return cleaned


def parse_section_text(soup, heading_text):
    heading = soup.find(
        lambda tag: tag.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong']
        and heading_text.lower() in tag.get_text().lower()
    )
    if not heading:
        return ""
    content_pieces = []
    current_element = heading.next_sibling
    while current_element:
        if current_element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            break
        if current_element.name in ['p', 'ul', 'ol', 'div', 'span']:
            txt = current_element.get_text(separator=' ', strip=True)
            if txt:
                content_pieces.append(txt)
        elif current_element.name is None:
            raw_str = current_element.strip()
            if raw_str:
                content_pieces.append(raw_str)
        current_element = current_element.next_sibling
    return "\n".join(content_pieces) if content_pieces else ""


def score_module_deterministically(module_record, prog_spec_text):
    scores_payload = {}

    cleaned_sections = {
        section: clean_academic_boilerplate(
            prog_spec_text if section == "programme_specification" else module_record.get(section, "")
        )
        for section in SECTION_WEIGHTS.keys()
    }

    for sdg, tier_dict in SDG_KEYWORDS.items():
        total_weighted_hits = 0.0
        evidence_segments = []

        for section, sec_weight in SECTION_WEIGHTS.items():
            text_context = cleaned_sections[section]
            if not text_context:
                continue

            section_raw_hits = 0
            matched_terms = []

            for tier_name, terms in tier_dict.items():
                tier_mult = TIER_MULTIPLIERS[tier_name]
                for term in terms:
                    matches = re.findall(r'\b' + re.escape(term), text_context, re.IGNORECASE)
                    if matches:
                        count = len(matches)
                        section_raw_hits += count
                        total_weighted_hits += (count * sec_weight * tier_mult)
                        if term not in matched_terms:
                            matched_terms.append(term)

            if section_raw_hits > 0:
                sample_terms = ", ".join(matched_terms[:4])
                evidence_segments.append(
                    f"{section.replace('_', ' ').title()}: {section_raw_hits} hits ({sample_terms})"
                )

        if total_weighted_hits == 0:
            score, classification = 0, "None"
        elif total_weighted_hits <= 2.2:
            score, classification = 1, "Some (Implicit)"
        else:
            score, classification = 2, "Significant (Explicit)"

        scores_payload[sdg] = {
            "score": score,
            "weighted_value": round(total_weighted_hits, 2),
            "classification": classification,
            "evidence_math_summary": "; ".join(evidence_segments) if evidence_segments else "No keywords matched"
        }
    return scores_payload


def crawl_single_module(mod_code, mod_title, mod_credits, mod_semester, module_absolute_url, headers, prog_spec_text):
    module_record = {
        "code": mod_code,
        "title": mod_title,
        "credits": mod_credits,
        "semester": mod_semester,
        "module_url": module_absolute_url,
        "module_summary": "",
        "objectives": "",
        "learning_outcomes": "",
        "syllabus": ""
    }
    try:
        mod_resp = requests.get(module_absolute_url, timeout=8, headers=headers)
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
        response = requests.get(programme_url, timeout=15, headers=headers)
        response.raise_for_status()
    except Exception as e:
        return None, f"Network fault on base URL: {str(e)}"

    soup = BeautifulSoup(response.text, 'html.parser')
    prog_spec_heading = soup.find(
        lambda tag: tag.name in ['h1', 'h2', 'h3', 'h4'] and "programme specification" in tag.get_text().lower()
    )
    prog_spec_content = ""
    if prog_spec_heading:
        curr = prog_spec_heading.next_sibling
        while curr:
            if curr.name in ['h1', 'h2', 'h3', 'table'] or (curr.name == 'div' and 'table-responsive' in curr.get('class', [])):
                break
            if curr.get_text:
                txt = curr.get_text(strip=True)
                if txt:
                    prog_spec_content += txt + "\n"
            curr = curr.next_sibling
    else:
        intro_box = soup.find('div', class_='programme-description')
        prog_spec_content = (
            intro_box.get_text(separator='\n', strip=True)
            if intro_box
            else soup.get_text(separator=' ', strip=True)[:1500]
        )

    page_title = soup.find('h1').get_text(strip=True) if soup.find('h1') else "Academic Programme Specification"
    prog_spec_cleaned = prog_spec_content.strip()

    data_payload = {
        "programme": {
            "title": page_title,
            "programme_specification": prog_spec_cleaned,
            "programme_sdg_scores": {}
        },
        "modules": []
    }

    all_tables = soup.find_all('table')
    visited_module_codes = set()
    crawl_tasks = []

    for table in all_tables:
        header_row = table.find('tr') or table.find('thead')
        if not header_row:
            continue
        columns = [th.get_text(strip=True).lower() for th in header_row.find_all(['th', 'td'])]
        if not all(col in columns for col in CORE_COLUMNS):
            continue
        col_indices = {col: columns.index(col) for col in CORE_COLUMNS}

        for row in table.find_all('tr')[1:]:
            cells = row.find_all(['td', 'th'])
            if len(cells) < len(columns):
                continue
            mod_code = cells[col_indices["code"]].get_text(strip=True)
            mod_title = cells[col_indices["title"]].get_text(strip=True)
            mod_credits = cells[col_indices["credits"]].get_text(strip=True)
            mod_semester = cells[col_indices["semester"]].get_text(strip=True)

            if not mod_code or len(mod_code) > 15 or mod_code in visited_module_codes:
                continue
            link_element = cells[col_indices["code"]].find('a', href=True) or cells[col_indices["title"]].find('a', href=True)
            if not link_element:
                continue

            module_absolute_url = urljoin(programme_url, link_element['href'])
            visited_module_codes.add(mod_code)
            crawl_tasks.append((mod_code, mod_title, mod_credits, mod_semester, module_absolute_url))

    if not crawl_tasks:
        return None, "No verified criteria layout tables found."

    with ThreadPoolExecutor(max_workers=min(12, len(crawl_tasks))) as executor:
        futures = {
            executor.submit(crawl_single_module, c, t, cr, s, u, headers, prog_spec_cleaned): c
            for c, t, cr, s, u in crawl_tasks
        }
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
@app.route('/SDGMapping')
@app.route('/sdgmapping')
def sdg_mapping():
    return render_template('Mapping.html')


@app.route('/api/scan', methods=['POST'])
def scan_website():
    data = request.get_json()
    target_url = data.get('url')
    if not target_url:
        return jsonify({"error": "No URL provided"}), 400

    structured_json, crawl_error = extract_and_score_curriculum(target_url)
    if crawl_error:
        return jsonify({"error": crawl_error}), 400

    return jsonify({
        "success": True,
        "programme_title": structured_json["programme"]["title"],
        "modules": [
            {
                "code": m["code"],
                "title": m["title"],
                "scores": {k: v["score"] for k, v in m["sdg_scores"].items()}
            }
            for m in structured_json["modules"]
        ],
        "programme_scores": {
            k: v["score"] for k, v in structured_json["programme"]["programme_sdg_scores"].items()
        },
        "raw_payload": structured_json
    })


@app.route('/api/chat', methods=['POST'])
def chat_with_curriculum():
    data = request.get_json()
    user_message = data.get('message', '').strip()
    history = data.get('history', [])
    module_data = data.get('module_data')
    programme_data = data.get('programme_data')

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return jsonify({"error": "GEMINI_API_KEY is not set in Render Environment Variables."}), 500

    if module_data:
        matched_sdgs = {
            k: v for k, v in module_data.get("sdg_scores", {}).items() if v.get("score", 0) > 0
        }
        scores_json = json.dumps(matched_sdgs, indent=2) if matched_sdgs else "No SDGs scored above 0."
        context_block = (
            "TARGET MODULE: " + str(module_data.get('code')) + " - " + str(module_data.get('title')) +
            " (Credits: " + str(module_data.get('credits')) + ", Semester: " + str(module_data.get('semester')) + ")\n" +
            "MODULE SUMMARY: " + (module_data.get('module_summary') or "Not provided") + "\n" +
            "OBJECTIVES: " + (module_data.get('objectives') or "Not provided") + "\n" +
            "LEARNING OUTCOMES: " + (module_data.get('learning_outcomes') or "Not provided") + "\n" +
            "SYLLABUS: " + (module_data.get('syllabus') or "Not provided") + "\n\n" +
            "DETERMINISTIC SDG SCORES & EVIDENCE FOR THIS MODULE:\n" + scores_json
        )
    else:
        prog_title = programme_data.get("title", "Unknown Programme") if programme_data else "Unknown Programme"
        prog_scores = programme_data.get("programme_sdg_scores", {}) if programme_data else {}
        mod_summaries = data.get("all_modules_summary", [])
        context_block = (
            "TARGET SCOPE: ENTIRE DEGREE PROGRAMME - " + str(prog_title) + "\n" +
            "PROGRAMME-LEVEL NET SDG SCORES:\n" + json.dumps(prog_scores, indent=2) + "\n\n" +
            "ALL MODULES IN PROGRAMME (Code, Title, and SDGs with score > 0):\n" +
            json.dumps(mod_summaries, indent=2)
        )

    system_instruction = (
        "You are an expert UK Higher Education Curriculum & Education for Sustainable Development (ESD) Advisor "
        "embedded inside the Institutional SDG Curriculum Analytics System.\n\n"
        "Your role is to help university lecturers, programme leaders, and quality assurance reviewers understand "
        "how their modules align with the 17 UN Sustainable Development Goals (SDGs) and provide practical, constructive pedagogical advice.\n\n"
        "Rules:\n"
        "1. Ground your answers strictly in the provided module/programme syllabus data and deterministic scoring evidence below. Do not invent syllabus topics that are not in the text.\n"
        "2. Remember how our scoring system works: Learning Outcomes carry weight 1.0, Syllabus 0.8, Objectives 0.5, Summary 0.2. "
        "Explicit sustainability keywords carry a 1.0 multiplier, while broad supportive terms carry 0.45. A score > 2.2 gives rating 2 (Significant/Explicit); > 0 to 2.2 gives rating 1 (Some/Implicit).\n"
        "3. When asked how to improve a module's SDG alignment, suggest concrete, academically rigorous rewrites of Learning Outcomes "
        "(using Bloom's taxonomy verbs) or syllabus topics tailored to the module's specific discipline.\n"
        "4. Use clear, concise UK English with bullet points and bold headings where helpful.\n\n"
        "CURRICULUM CONTEXT:\n" + context_block
    )

    contents = []
    for turn in history[-8:]:
        gemini_role = "model" if turn["role"] == "assistant" else "user"
        contents.append({
            "role": gemini_role,
            "parts": [{"text": turn["content"]}]
        })
    contents.append({
        "role": "user",
        "parts": [{"text": user_message}]
    })

    gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + gemini_key
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 1200
        }
    }

    try:
        resp = requests.post(gemini_url, json=payload, timeout=30)
        if not resp.ok:
            err_detail = resp.text
            try:
                err_json = resp.json()
                err_detail = err_json.get("error", {}).get("message", resp.text)
            except Exception:
                pass
            logging.error(f"Gemini API HTTP {resp.status_code}: {err_detail}")
            return jsonify({"error": f"Gemini API Error ({resp.status_code}): {err_detail}"}), 500

        resp_data = resp.json()
        reply_text = resp_data["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"reply": reply_text})

    except Exception as e:
        logging.error(f"Gemini Chat Exception: {e}")
        return jsonify({"error": f"AI service error: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)