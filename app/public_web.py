"""Public sign-up pages (no login): schools ask for help, partners offer help.

PS43 can share /join in its outreach. Each form becomes one intake item, exactly like a WhatsApp message:
it lands in the inbox, the AI writes the case draft, and staff review it before anything happens.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import db
from .processing import pipeline

router = APIRouter(tags=["public"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

REGIONS = ["Adamawa", "Centre", "East", "Far North", "Littoral", "North", "Northwest", "South", "Southwest", "West"]
NEEDS = ["classrooms_building", "roofing", "water", "sanitation_latrines", "furniture", "electricity", "devices",
         "connectivity", "learning_materials", "teacher_training", "fees_and_access", "birth_registration",
         "school_meals_health", "safety_security"]
GIVES = ["money", "goods", "services", "volunteers", "transport", "expertise"]

T = {
    "fr": {
        "title": "PS43 Foundation : écoles et partenaires", "title_short": "Ensemble pour les écoles du Cameroun", "lang_other": "English", "lang_other_code": "en",
        "intro": "PS43 aide les écoles du Cameroun à obtenir des salles de classe, de l'eau, de l'électricité, des ordinateurs et du matériel, avec des partenaires de confiance.",
        "school_cta": "Mon école a besoin d'aide", "partner_cta": "Je veux aider (don, travaux, transport)",
        "school_title": "Demander de l'aide pour une école", "partner_title": "Proposer de l'aide",
        "school_name": "Nom de l'école", "town": "Ville ou village", "region": "Région", "your_name": "Votre nom",
        "your_role": "Votre rôle (directeur, enseignant, parent...)", "phone": "Numéro WhatsApp", "pupils": "Nombre d'élèves",
        "needs": "De quoi l'école a-t-elle besoin ?", "details": "Expliquez la situation en quelques phrases",
        "org_name": "Votre nom ou celui de votre organisation", "kind": "Vous êtes", "country": "Pays où vous êtes",
        "gives": "Que pouvez-vous offrir ?", "offer_details": "Quoi exactement, et combien ? (ex. 50 ordinateurs portables, 400 tôles, 2 000 000 FCFA)",
        "where": "Où pouvez-vous aider ?", "national": "Partout au Cameroun",
        "abroad": "Les articles sont hors du Cameroun (il faudra les expédier)",
        "email": "E-mail (si pas de WhatsApp)",
        "consent": "J'accepte que PS43 conserve ces informations pour traiter ma demande et me contacte sur WhatsApp. Je peux demander leur suppression à tout moment (envoyez STOP).",
        "send": "Envoyer", "thanks": "Merci ! Votre message est bien arrivé.",
        "wa_title": "Plus simple : WhatsApp", "wa_text": "Écrivez-nous ou envoyez une note vocale, en français, en anglais ou en pidgin. Vous pouvez aussi envoyer des photos.",
        "wa_button": "Écrire sur WhatsApp", "wa_school": "Bonjour PS43, je vous écris pour notre école. Nom de l'école et ville : ",
        "wa_partner": "Bonjour PS43, je voudrais aider des écoles. Je peux offrir : ",
        "more": "Plus de détails (facultatif)", "or_form": "ou remplissez le formulaire",
        "thanks_more": "Une personne de PS43 va le lire et vous écrira sur WhatsApp. Vous pouvez aussi envoyer des photos ou une note vocale sur WhatsApp.",
        "missing": "Merci de remplir les champs obligatoires et de cocher l'accord.",
        "need_labels": {"classrooms_building": "Salles de classe / bâtiment", "roofing": "Toiture", "water": "Eau potable",
                        "sanitation_latrines": "Toilettes / latrines", "furniture": "Table-bancs", "electricity": "Électricité",
                        "devices": "Ordinateurs / tablettes", "connectivity": "Internet", "learning_materials": "Livres et matériel",
                        "teacher_training": "Formation des enseignants", "fees_and_access": "Frais de scolarité",
                        "birth_registration": "Actes de naissance", "school_meals_health": "Cantine / santé",
                        "safety_security": "Sécurité"},
        "give_labels": {"money": "De l'argent", "goods": "Du matériel", "services": "Des travaux / services",
                        "volunteers": "Des bénévoles", "transport": "Du transport / expédition", "expertise": "Des conseils / expertise"},
        "kind_labels": {"individual": "Un particulier", "diaspora_group": "Un groupe de la diaspora", "company": "Une entreprise",
                        "ngo": "Une ONG / association", "foundation": "Une fondation", "faith_group": "Un groupe religieux",
                        "other": "Autre"},
    },
    "en": {
        "title": "PS43 Foundation: schools and partners", "title_short": "Together for Cameroon's schools", "lang_other": "Français", "lang_other_code": "fr",
        "intro": "PS43 helps schools in Cameroon get classrooms, water, power, computers and materials, with partners we trust.",
        "school_cta": "My school needs help", "partner_cta": "I want to help (donate, build, ship)",
        "school_title": "Ask for help for a school", "partner_title": "Offer help",
        "school_name": "School name", "town": "Town or village", "region": "Region", "your_name": "Your name",
        "your_role": "Your role (head teacher, teacher, parent...)", "phone": "WhatsApp number", "pupils": "Number of pupils",
        "needs": "What does the school need?", "details": "Explain the situation in a few sentences",
        "org_name": "Your name or your organisation's", "kind": "You are", "country": "Country you are in",
        "gives": "What can you offer?", "offer_details": "What exactly, and how many? (e.g. 50 laptops, 400 roofing sheets, 2,000,000 FCFA)",
        "where": "Where can you help?", "national": "Anywhere in Cameroon",
        "abroad": "The items are outside Cameroon (they will need shipping)",
        "email": "Email (if no WhatsApp)",
        "consent": "I agree that PS43 keeps this information to handle my request and contacts me on WhatsApp. I can ask for it to be deleted at any time (send STOP).",
        "send": "Send", "thanks": "Thank you! Your message has arrived.",
        "wa_title": "Easier: WhatsApp", "wa_text": "Write to us or send a voice note, in English, French or Pidgin. You can send photos too.",
        "wa_button": "Write on WhatsApp", "wa_school": "Hello PS43, I am writing about our school. School name and town: ",
        "wa_partner": "Hello PS43, I would like to help schools. I can offer: ",
        "more": "More details (optional)", "or_form": "or fill in the form",
        "thanks_more": "Someone from PS43 will read it and write to you on WhatsApp. You can also send photos or a voice note on WhatsApp.",
        "missing": "Please fill in the required fields and tick the agreement.",
        "need_labels": {"classrooms_building": "Classrooms / building", "roofing": "Roofing", "water": "Drinking water",
                        "sanitation_latrines": "Toilets / latrines", "furniture": "Desks and benches", "electricity": "Electricity",
                        "devices": "Computers / tablets", "connectivity": "Internet", "learning_materials": "Books and materials",
                        "teacher_training": "Teacher training", "fees_and_access": "School fees",
                        "birth_registration": "Birth certificates", "school_meals_health": "Meals / health",
                        "safety_security": "Safety"},
        "give_labels": {"money": "Money", "goods": "Goods / equipment", "services": "Building work / services",
                        "volunteers": "Volunteers", "transport": "Transport / shipping", "expertise": "Advice / expertise"},
        "kind_labels": {"individual": "A person", "diaspora_group": "A diaspora group", "company": "A company",
                        "ngo": "An NGO / association", "foundation": "A foundation", "faith_group": "A faith group",
                        "other": "Other"},
    },
}


def whatsapp_link(lang: str, form: str | None) -> str | None:
    """wa.me link to PS43's WhatsApp with a ready-to-send first line, or None if no number is set."""
    from urllib.parse import quote
    from .config import settings
    number = re.sub(r"\D", "", settings.public_whatsapp or "")
    if len(number) < 8:
        return None
    t = _t(lang)
    text = t["wa_partner"] if form == "partner" else t["wa_school"]
    return f"https://wa.me/{number}?text={quote(text)}"


def _t(lang: str) -> dict:
    return T["en" if lang == "en" else "fr"]


def _page(request: Request, name: str, lang: str, status_code: int = 200, **kw):
    lang = "en" if lang == "en" else "fr"
    ctx = {"request": request, "t": _t(lang), "lang": lang, "regions": REGIONS, "needs": NEEDS, "gives": GIVES,
           "wa": whatsapp_link(lang, kw.get("form")), **kw}
    return templates.TemplateResponse(request, name, ctx,
                                      status_code=status_code)


def _clip(text: str, n: int = 2000) -> str:
    return (text or "").strip()[:n]


def _address(phone: str, email: str, name: str) -> str:
    digits = re.sub(r"[^\d+]", "", phone or "")
    if len(re.sub(r"\D", "", digits)) >= 8:
        return "whatsapp:" + (digits if digits.startswith("+") else "+" + digits)
    if email and "@" in email:
        return "web:" + email.strip().lower()[:200]
    return "web:" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "anonymous")


def _store(address: str, name: str, body: str) -> int:
    contact = db.get_or_create_contact(address, "web", name or None)
    if contact["consent_status"] != "opted_out":
        db.set_consent(contact["id"], "granted")  # ticked on the form
    item = db.add_item(contact_id=contact["id"], channel="web", kind="form", body=body, author=name or None)
    pipeline.enqueue(item["id"])
    return item["thread_id"]


@router.get("/join", response_class=HTMLResponse)
def join(request: Request, lang: str = "fr"):
    return _page(request, "join.html", lang, form=None)


@router.get("/join/school", response_class=HTMLResponse)
def join_school(request: Request, lang: str = "fr"):
    return _page(request, "join.html", lang, form="school")


@router.get("/join/partner", response_class=HTMLResponse)
def join_partner(request: Request, lang: str = "fr"):
    return _page(request, "join.html", lang, form="partner")


@router.post("/join/school", response_class=HTMLResponse)
def join_school_post(request: Request, lang: str = Form("fr"), school_name: str = Form(""), town: str = Form(""),
                     region: str = Form(""), your_name: str = Form(""), your_role: str = Form(""), phone: str = Form(""),
                     email: str = Form(""), pupils: str = Form(""), needs: list[str] = Form([]), details: str = Form(""),
                     consent: str = Form(""), website: str = Form("")):
    if website:  # hidden field: only bots fill it
        return _page(request, "join.html", lang, form=None, done=True)
    if not (school_name.strip() and (phone.strip() or email.strip()) and consent):
        return _page(request, "join.html", lang, form="school", error=_t(lang)["missing"], status_code=400)
    labels = T["en"]["need_labels"]
    body = "\n".join([
        "Website form: a school asks PS43 for help",
        f"School name: {_clip(school_name, 200)}", f"Town: {_clip(town, 120)}", f"Region: {_clip(region, 60)}",
        f"Contact person: {_clip(your_name, 120)} ({_clip(your_role, 120)})", f"Phone/WhatsApp: {_clip(phone, 40)}",
        f"Email: {_clip(email, 200)}" if email.strip() else "",
        f"Pupils: {_clip(pupils, 20)}" if pupils.strip() else "",
        "Needs ticked: " + ", ".join(labels.get(n, n) for n in needs if n in labels) if needs else "",
        f"In their words: {_clip(details)}" if details.strip() else "",
    ])
    _store(_address(phone, email, school_name), _clip(school_name, 200), "\n".join(l for l in body.split("\n") if l))
    return _page(request, "join.html", lang, form=None, done=True)


@router.post("/join/partner", response_class=HTMLResponse)
def join_partner_post(request: Request, lang: str = Form("fr"), org_name: str = Form(""), kind: str = Form(""),
                      country: str = Form(""), your_name: str = Form(""), phone: str = Form(""), email: str = Form(""),
                      gives: list[str] = Form([]), offer_details: str = Form(""), regions: list[str] = Form([]),
                      abroad: str = Form(""), consent: str = Form(""), website: str = Form("")):
    if website:
        return _page(request, "join.html", lang, form=None, done=True)
    if not (org_name.strip() and (phone.strip() or email.strip()) and offer_details.strip() and consent):
        return _page(request, "join.html", lang, form="partner", error=_t(lang)["missing"], status_code=400)
    en = T["en"]
    body = "\n".join(l for l in [
        "Website form: a partner offers help to PS43",
        f"Name / organisation: {_clip(org_name, 200)}",
        f"Type: {en['kind_labels'].get(kind, kind)}" if kind else "",
        f"Contact person: {_clip(your_name, 120)}" if your_name.strip() else "",
        f"Country: {_clip(country, 80)}" if country.strip() else "",
        f"Phone/WhatsApp: {_clip(phone, 40)}", f"Email: {_clip(email, 200)}" if email.strip() else "",
        "Can offer: " + ", ".join(en["give_labels"].get(g, g) for g in gives if g in en["give_labels"]) if gives else "",
        f"Details (what and how many): {_clip(offer_details)}",
        "Can help in: " + ", ".join(r for r in regions if r in REGIONS or r == "National") if regions else "",
        f"Items are outside Cameroon and need shipping (currently in {_clip(country, 80) or 'another country'})" if abroad else "",
    ] if l)
    _store(_address(phone, email, org_name), _clip(org_name, 200), body)
    return _page(request, "join.html", lang, form=None, done=True)
