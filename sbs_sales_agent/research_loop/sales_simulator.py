from __future__ import annotations

import re
import random
from typing import Any

from ..config import AgentSettings
from ..integrations.ollama_client import OllamaClient
from .business_sampler import SampledBusiness
from .types import SalesSimulationScenario, validate_sales_reply_payload


SCENARIOS = [
    ("skeptical_owner", "Skeptical owner who doubts automated audits."),
    ("price_sensitive", "Price-sensitive owner asking why report is worth $299."),
    ("technical_operator", "Technical operator asks for proof and exact fixes."),
    ("busy_decider", "Busy decision maker wants TL;DR and timeline."),
    ("curveball_scope", "Requests extra scope and custom competitor analysis."),
    ("compliance_cautious", "Concerned about legal/compliance liability and evidence quality."),
    ("refund_risk", "Asks about dissatisfaction/refund confidence."),
    ("timeline_pressure", "Needs report same day and asks if actionable immediately."),
    ("comparison_shopper", "Owner who has spoken to other vendors and asks how this differs."),
    ("repeat_skeptic", "Owner who raises a second skeptical objection after the first is handled."),
    ("already_has_agency", "Owner who already works with a web agency and questions the value of a separate audit."),
    ("data_privacy_concerned", "Owner worried about what data was collected from their site during the scan."),
    ("overwhelmed_owner", "Owner who knows they have issues but feels overwhelmed and doesn't know where to start."),
    ("seo_focused_buyer", "Owner who cares primarily about Google rankings and questions why security/ADA matter."),
    ("mobile_first_buyer", "Owner whose customers mostly use phones and wants to know how findings affect mobile visitors."),
    ("accessibility_attorney", "Business owner who received an ADA demand letter and needs specific WCAG violation evidence."),
    ("performance_anxious", "Owner who just ran Google PageSpeed Insights and got a score below 50, now anxious about site speed."),
    ("roi_focused_buyer", "Analytically minded owner who wants exact ROI calculations, payback period, and break-even timeline before committing."),
    ("quick_start_buyer", "Owner who is already convinced but just wants the 2–3 things to do in the next 48 hours — minimal back-and-forth."),
    ("cybersecurity_worried", "Owner whose domain was recently used to send phishing emails to customers, or who noticed suspicious unauthorized access attempts. Wants immediate security action."),
    ("franchise_owner", "Part of a franchise network. Needs corporate approval for site changes and must justify any work to brand standards. Focused on compliance documentation."),
    ("healthcare_compliance_buyer", "Small healthcare practice owner concerned about HIPAA violations, PHI exposure via contact forms, and DOJ ADA enforcement. Needs thorough compliance documentation before acting."),
    ("ecommerce_cro_owner", "E-commerce store owner focused on cart abandonment and checkout conversion rates. Wants specific funnel friction points, load time findings, and trust signals that directly affect purchase completion."),
    ("social_proof_seeker", "Owner who relies heavily on word-of-mouth and wants to know how the report methodology is validated — asks for case studies, methodology transparency, and proof the recommendations actually work."),
    ("enterprise_it_manager", "IT manager at a mid-size company evaluating the report for technical depth, CVSS-level severity ratings, integration with existing vulnerability management processes, and auditability of findings."),
    ("budget_constrained_nonprofit", "Executive director of a small nonprofit with a minimal IT budget. Focused on ADA compliance (DOJ enforcement risk) and free/low-cost remediation options. Wants to know which fixes require developer time vs. plugin installs."),
    ("multi_location_owner", "Owner of a business with 3+ physical locations who wants to know which location website has the worst issues and whether findings apply uniformly across all sites. Focused on local SEO across locations."),
    ("local_seo_buyer", "Brick-and-mortar business owner whose revenue depends heavily on Google Maps 'near me' searches and the local 3-pack. Worried about NAP (Name/Address/Phone) consistency, Google Business Profile optimization, review schema, and why competitors rank above them locally."),
    ("gdpr_anxious_buyer", "Small business with EU customers or GDPR-adjacent exposure (US state privacy laws, CCPA). Worried about cookie consent implementation, contact form data handling, breach notification requirements, and whether the audit itself collected any visitor data improperly."),
    ("restaurant_owner", "Local restaurant owner whose revenue depends on Google Maps visibility, online menu SEO, review management, and table-booking / order CTA conversion. Wants to know how site issues affect reservations and local search ranking."),
    ("legal_professional", "Law firm owner navigating bar association restrictions on digital advertising, client confidentiality risks in contact forms, and ADA demand letter exposure. Needs documentation for ethics/compliance review."),
    ("referral_partner", "Business owner referred by their accountant or attorney. Deeply skeptical of digital marketing claims after being burned by an agency previously. Demands peer references, case studies, and hard numerical ROI proof before committing."),
    ("review_reputation_buyer", "Business owner whose Google review rating dropped after a bad customer experience went viral. Fixated on online reputation, review schema markup, star ratings in search results, and Google Business Profile optimization as the primary purchase driver."),
    ("b2b_saas_founder", "SaaS startup founder selling to enterprise customers. Worried about GDPR compliance, security posture for SOC 2 readiness, trust signal issues that affect enterprise buyer confidence, and conversion funnel optimization for a free-trial signup flow."),
    ("home_services_owner", "Plumber, HVAC technician, or electrician owner whose entire business depends on phone calls from Google Maps 'near me' searches. Not technically savvy, skeptical of anything digital, but acutely aware that a competitor ranks above them in the local 3-pack."),
    ("dental_practice_owner", "Dental or medical practice owner focused on patient acquisition via Google local search, HIPAA-adjacent privacy concerns for contact/appointment forms, ADA compliance for elderly and disabled patients, and building a consistent stream of new patient bookings."),
    ("fitness_studio_owner", "Yoga, gym, or fitness studio owner whose class bookings are driven by Instagram, Google Maps, and mobile search. Wants class booking CTAs, mobile performance findings, and local SEO insights to outrank competitor studios in the same zip code."),
    ("print_media_traditionalist", "Long-established business owner (30+ years) whose advertising history is Yellow Pages, local newspaper, and radio. Deeply skeptical of 'digital stuff', doubts customers even find businesses via Google Search, and needs the ROI case made in plain non-technical language tied to concrete outcomes like phone calls and walk-ins."),
    ("first_time_website_owner", "Business owner who launched their first website less than 6 months ago and doesn't know what terms like WCAG, DMARC, or meta description mean. Overwhelmed by jargon, wants everything explained in plain English, and primarily needs a clear 'what should I do first' priority list rather than technical depth."),
    ("budget_approval_needed", "Operations director or mid-size business owner whose purchases over $500 require CFO or board approval. Has genuine interest in the report findings but cannot commit without a concise executive-friendly ROI summary, specific payback period, and competitive risk framing to present internally. Needs the business case packaged for a non-technical decision-maker."),
    ("already_has_seo_agency", "Business owner currently paying $1,200–$1,800/month to an SEO agency. Questions whether this report overlaps with what the agency already checks and whether paying for both makes sense. Needs a clear explanation of what a web presence risk + growth report covers that a standard SEO retainer does not — security posture, ADA compliance, email authentication, and conversion UX."),
    ("insurance_agent_owner", "Independent insurance agency owner who handles sensitive client prospect data through online quote request forms. Deeply concerned about E&O (errors & omissions) liability from data exposure, email spoofing risk from their domain being used to send fraudulent policy quotes, and ADA compliance for elderly and disabled clients who rely on their website for policy information and claims inquiries."),
    ("childcare_provider_owner", "Daycare or preschool owner focused on parent-facing mobile experience for enrollment inquiries, online registration form security, and ADA compliance for parents with disabilities who need accessible scheduling information. Wants Google Maps visibility for 'daycare near me' searches and understands their enrollment pipeline depends heavily on appearing in local search results and having a trustworthy, secure online presence."),
    ("physical_therapist_owner", "Physical therapy clinic owner whose patients include elderly and mobility-impaired individuals. Deeply focused on ADA compliance (accessible booking forms, keyboard navigation, sufficient color contrast for low-vision patients), HIPAA-adjacent privacy concerns for online intake forms, and local SEO for 'physical therapy near me' searches that drive new patient acquisition."),
    ("auto_repair_shop_owner", "Auto repair shop owner whose entire business depends on Google reviews, star ratings in search results, and 'auto repair near me' calls. Skeptical of technical jargon and wants all findings explained in terms of phone calls, walk-ins, and Google Maps visibility. Has limited technical knowledge but is highly motivated by competitive local search positioning."),
    ("accountant_practice_owner", "CPA or bookkeeping firm owner deeply concerned about client data confidentiality — tax returns, financial statements, and personal income data flow through their contact forms and client portal. Worried about email spoofing risk from their domain being used to send fraudulent tax notices or IRS impersonation messages. Values professional reputation and wants to understand how report findings could lead to malpractice exposure or client trust erosion if exploited."),
    ("veterinary_clinic_owner", "Local veterinary clinic owner focused on appointment booking CTA conversion (online vs. phone calls), Google Maps visibility for 'vet near me' and 'emergency vet' searches, and pet owner reviews influencing new client acquisition. Wants mobile experience optimized for anxious pet owners searching from their phones. Concerned about load time for mobile users in emergency situations and social proof signals (reviews schema, star ratings) that influence which clinic a panicked owner calls first."),
    ("property_management_owner", "Property management company owner with rental listing websites targeting prospective tenants. Worried about local search visibility for 'property management near me' and '[city] apartments for rent', Fair Housing Act ADA compliance obligations for disabled tenant accessibility, lead capture form security for sensitive application data (SSN, income, references), and mobile experience for renters searching on phones. Wants to rank above competing property managers in local search and understand which conversion friction points are causing applicants to abandon mid-form."),
    ("nonprofit_board_member", "Board member or executive director of a 501(c)(3) nonprofit that relies on online donation forms and grant funding. Concerned about Section 508 / WCAG ADA compliance as a grant eligibility requirement, donor trust signals on the donation page, email authentication to prevent fraudulent fundraising emails impersonating the organization, and GDPR/CCPA considerations for donor data. Has limited IT budget and needs free or low-cost remediation paths. Several major funders have started requiring WCAG 2.1 AA accessibility as a condition of grants."),
    ("tutoring_center_owner", "SAT/ACT prep or K-12 tutoring center owner whose enrollment pipeline runs through Google search ('tutoring near me', 'SAT prep [city]'), parent reviews on Google and Yelp, and an online inquiry form. Concerned about student privacy (FERPA-adjacent obligations for minors' data), load speed for parent searches on mobile, Google Maps visibility, and click-to-call for parents in decision mode. Wants findings framed in terms of enrollment inquiries and new student acquisition rather than technical jargon."),
    ("boutique_hotel_owner", "Independent boutique hotel or B&B owner determined to win direct bookings over OTA platforms (Expedia, Booking.com) that charge 15–25% commission per reservation. Obsessed with conversion friction: slow image-heavy gallery pages, unclear room pricing, no above-fold booking CTA, and missing review schema for Google Hotel Pack star ratings. Mobile speed is critical because 60%+ of leisure travel searches begin on phone. Wants to understand which specific page-level issues are pushing potential guests toward OTAs."),
    ("photography_studio_owner", "Professional photographer or photography studio owner whose portfolio website is their primary conversion engine. Acutely aware that slow mobile gallery pages kill booking inquiries, but doesn't know which specific performance issues are causing it. Wants findings linked to Instagram/social proof, client booking form security, image schema for Google Images SEO, and how competitor studios are outranking them for 'photographer near me' searches. Judges the report by whether it explains why their galleries load slowly and what they can realistically fix themselves without a developer."),
    ("financial_advisor_owner", "Independent RIA, CFP, or financial planning firm owner in a heavily regulated industry (SEC/FINRA advertising rules prohibit unsubstantiated performance claims online). Extremely concerned about email authentication because domain spoofing is used in financial scams targeting their clients. ADA compliance matters because many of their retired clients have low vision or motor impairments. Online forms collect sensitive financial data and needs confirmation that contact/inquiry forms are secure and compliant. Wants the report to map each finding to a regulatory or liability consequence, not just technical severity."),
    ("optometry_practice_owner", "Optometrist or eye care practice owner whose patient base skews older (40–75+), making ADA accessibility for low-vision users an ironic but real concern — patients with presbyopia or early macular degeneration may struggle with their own eye doctor's website. Focused on local SEO for 'eye exam near me' and 'optometrist near me' searches, online appointment booking form security for vision insurance data, review schema for Google local pack star ratings, and click-to-call for elderly patients who prefer phone scheduling. Increasingly worried about HIPAA-adjacent privacy obligations from online appointment and insurance inquiry forms. Wants findings ranked by impact on new patient acquisition and patient trust."),
    ("landscaping_business_owner", "Landscaping, lawn care, or outdoor services company owner whose leads come almost entirely from Google Maps 'landscaping near me' and 'lawn service [city]' searches and referrals from neighbor-to-neighbor word of mouth. Not technically savvy but highly competitive locally — angry that a smaller competitor ranks above them despite having fewer reviews. Wants to understand exactly which local SEO gaps explain the ranking difference. Also worried about mobile experience because homeowners search from their phones while looking at their yard. Finds technical jargon frustrating and wants every finding explained in terms of calls, quotes submitted, and seasonal booking volume."),
    ("wedding_venue_owner", "Independent wedding venue or event space owner whose bookings are driven by Google searches ('wedding venues near me', 'outdoor wedding venue [city]'), bridal show leads, and WeddingWire/The Knot referrals. Acutely aware that brides research on their phones and immediately judge venues by website gallery speed and visual quality. Concerned about slow image-heavy gallery pages losing potential clients, above-fold booking inquiry CTAs, review schema for Google local pack star ratings, and why a newer competitor with fewer reviews ranks above them. Peak inquiry season is January–March. Not technical, but very competitive about every lost inquiry."),
    ("e_learning_platform_owner", "Online course creator or e-learning platform owner selling digital courses to a global audience. Worried about WCAG accessibility for screen-reader-dependent students with visual or motor impairments, GDPR/CCPA compliance because students enroll from EU countries and US states with privacy laws, LMS enrollment form security for payment and personal data, and page load performance for video-heavy course pages. Also concerned about Google search visibility for '[subject] online course' queries. Needs to understand which findings create legal liability vs. which are conversion improvements, and wants the report to map each issue to a concrete student experience impact."),
    ("chiropractor_practice_owner", "Chiropractic or physical wellness practice owner focused on new patient acquisition via 'chiropractor near me' and 'back pain relief [city]' Google searches. Competes with 8–12 local practices in the same zip code. ADA accessibility matters because many patients have mobility limitations, chronic pain, or low vision that affect how they interact with booking forms and appointment pages. Concerned about HIPAA-adjacent privacy on intake forms that ask for health history, insurance, and pain conditions. Wants review schema enabled so 5-star Google ratings appear in local pack, and click-to-call optimized for patients searching during a pain episode who need to book immediately. Not technical — wants everything explained in terms of new patient bookings and phone calls."),
    ("tech_startup_cto", "CTO or VP Engineering at a B2B SaaS startup (10–50 employees) actively pursuing enterprise customer deals. Enterprise procurement teams are requesting vendor security questionnaires that include web property security posture. Concerned about OWASP Top 10 compliance on their marketing site, GDPR/CCPA obligations from contact form data and cookie consent, and whether public-facing security findings could surface in enterprise due diligence reviews. Wants findings mapped to CVSS severity levels and OWASP categories for internal security tracking. Also interested in ADA compliance because enterprise customers in financial services and healthcare ask about accessibility as part of vendor evaluation. Developer-friendly — wants exact remediation steps, not marketing language."),
    ("spa_salon_owner", "Day spa, hair salon, or beauty studio owner whose appointment bookings flow entirely through an online booking widget (Vagaro, Mindbody, Square Appointments, or custom form). Concerned about mobile-first experience because 80%+ of clients search 'nail salon near me' or 'blow dry bar [city]' from their phones. Wants to understand why competitors with fewer Google reviews rank above them in the local 3-pack, and whether missing schema markup or slow gallery images are hurting their booking conversion rate. ADA matters because some clients have disabilities affecting how they use booking forms. Doesn't want technical jargon — frames everything in terms of client appointments, repeat visits, and new customer acquisition from Google searches."),
    ("real_estate_agent_owner", "Independent real estate agent or small brokerage owner whose entire lead pipeline depends on appearing in Google searches for '[city] homes for sale', '[neighborhood] real estate agent', and 'sell my house [city]'. Already uses Zillow/Realtor.com but knows organic search leads convert better. Concerned that their website IDX property listings load too slowly, their mobile experience loses buyers mid-scroll, and their contact/home-valuation forms have friction that causes abandonment. Email authentication matters because the real estate sector is a high-value target for wire transfer fraud via domain spoofing — many agents have had clients lose earnest money to fraudulent emails impersonating agents. Wants the report to tie every finding back to leads, listings views, or closing volume."),
    ("franchise_expansion_buyer", "Franchise operator currently running 3–5 locations with plans to expand. Their corporate franchisor requires consistent brand presentation and compliance across all location websites. They want to understand if the audit process can be systematized — run for each location independently — and whether issues found on their main site are likely replicated across all locations. Interested in bulk pricing or a volume package if they're buying reports for multiple sites. Asks how the report compares to what their franchisor's IT team provides. Focused on local SEO consistency (NAP citations, Google Business Profile synchronization across locations), ADA compliance for franchise-wide liability exposure, and whether email authentication failures on one location domain could affect the entire corporate domain's email reputation."),
    ("anxious_solopreneur", "One-person business (freelancer, coach, consultant, or home service provider) who built their website themselves using Wix, Squarespace, or a basic WordPress theme. Has no developers, no IT support, and a very limited budget. Gets overwhelmed and defensive when reports use technical terms without plain-English explanations. Their biggest fear is being told everything is broken and that fixing it will cost thousands of dollars. Primarily wants to understand: (1) what can they do themselves today without hiring anyone, (2) what is the single most important thing to fix first, and (3) whether the problems found actually matter for a one-person business or are only relevant for large companies. Responds well to reassurance, prioritization help, and 'you can do this in 10 minutes' type guidance."),
    ("nonprofit_executive_director", "Executive director or program director of a small 501(c)(3) nonprofit organization. Responsible to a board of directors who must approve any consulting or service spend. The nonprofit runs online donation campaigns and grant applications. Three critical concerns: (1) Section 508 / WCAG ADA compliance — several major foundation funders have started requiring WCAG 2.1 AA as a condition of grants; (2) email authentication — the domain has been spoofed before by scammers sending fraudulent fundraising emails that damaged donor trust and required an emergency email to all supporters; (3) donor confidence signals — the donation page needs security indicators (HTTPS, SSL, visible privacy policy) that assure skeptical donors their credit card data is safe. Has a minimal IT budget ($0 discretionary for digital) but can allocate from a capacity-building grant if there's a clear compliance rationale. Wants free or low-cost remediation paths identified and findings mapped to grant eligibility language."),
    ("tech_savvy_diy_owner", "Business owner who has read multiple SEO blogs (Moz, Ahrefs, Search Engine Journal), watches Gary Vaynerchuk and Neil Patel YouTube videos, and thinks they already know the key issues with their site. Comes in with strong preconceptions — 'I already know about meta descriptions' and 'I installed Yoast so my SEO is fine'. Genuinely curious but needs to be shown the issues they MISSED despite their self-education. Responds best to technical specificity and expert-level depth — generic advice frustrates them. Wants to see findings that go beyond surface-level SEO basics into areas they haven't considered: email authentication, ADA exposure, server-level security headers, structured data completeness, and conversion path friction. Their ego is tied to knowing their site well, so the report must show them specific things they haven't seen before without making them feel embarrassed. Will ask technical follow-up questions to test if the report is genuinely expert-grade or automated boilerplate."),
    ("cybersecurity_msp_prospect", "IT managed service provider (MSP) that supports 20–50 SMB clients across network security, hardware, and software needs. Exploring whether to add website security auditing as a new billable service offering to their existing client base. Asks how the web presence report compares to their current Qualys/Nessus vulnerability scans — the key distinction being that those tools assess the network and endpoint layer while this report covers the web application layer: security headers, WCAG ADA compliance, email authentication, and SEO structure. Interested in white-label report branding, batch pricing for multiple clients, and how findings could be exported to their PSA tools (ConnectWise, Autotask, Datto) as service tickets. Technical audience with strong IT background — not impressed by marketing language, wants to understand the detection methodology and whether the findings are actionable by their NOC team without specialized web developer knowledge."),
    ("interior_designer_owner", "Interior design studio or residential/commercial design firm owner whose website is their primary portfolio and conversion engine. Prospective clients (affluent homeowners, commercial property developers) judge design expertise by website quality before ever making contact — a slow, visually broken, or hard-to-navigate portfolio site directly loses high-value project inquiries worth $20K–$150K. Acutely concerned about gallery page performance (large interior photography loads slowly on mobile), Google Images SEO (wants portfolio photos to appear when clients search 'interior designer [city]' or '[style] interior design'), Instagram and Pinterest referral traffic converting into consultation bookings, and booking inquiry form security for clients sharing renovation budgets and property details. ADA matters because some clients are elderly or have visual impairments. Not interested in generic SEO basics — wants to understand the specific technical issues that separate a portfolio site that wins premium commissions from one that loses them to a faster, better-optimized competitor."),
]


_SCENARIO_FALLBACKS: dict[str, list[str]] = {
    "skeptical_owner": [
        "Every finding in this report is tied to a specific page URL with direct evidence — no guesswork. "
        "{hl0} was verified against live response headers and page content. Hand it to your developer and they'll know exactly what to fix.",
        "Reasonable skepticism. That's why the report includes page-level evidence with {finding_count} findings, each documented with the exact URL, severity, and remediation step. No fluff.",
        "The scan covers {finding_count} distinct issues across security, SEO, ADA, and conversion — all with page URLs your developer can verify in under 5 minutes.",
    ],
    "price_sensitive": [
        "At $299 you get {finding_count} specific, actionable issues — agencies charge $1,500–$3,000 for comparable audits. "
        "The 30-day roadmap alone saves 5+ hours of prioritization time.",
        "The ROI is concrete: fixing {hl0} alone typically drives a measurable conversion or ranking improvement within 30 days. Most clients recover the cost in the first month.",
        "Compare it to hiring an SEO agency ($1,000+/month) or a dev firm ($150+/hour). This gives you the exact fix list upfront so you're not paying for discovery time.",
    ],
    "technical_operator": [
        "{hl0} is documented with the exact page URL, HTTP response data, severity rating, and a step-by-step remediation path. "
        "Every high/critical finding includes the specific fix your developer can implement immediately.",
        "The report includes {finding_count} findings categorized by severity with evidence metadata. "
        "Security headers, TLS config, DNS auth records, and page-level ADA issues are all traceable to specific checks.",
        "High-confidence findings (≥80%) are marked ready for immediate action. Medium-confidence items are flagged for developer review before implementation.",
    ],
    "busy_decider": [
        "Short version: {hl0}, {hl1}, and {hl2} are your top urgent items. "
        "The 0–30 day roadmap tells your team exactly what to tackle first for the highest ROI.",
        "Top 3 urgent issues documented with effort estimates. Your developer can start on the first fix today — the remediation steps are included.",
        "The executive summary gives you a health score out of 100, top 5 issues, and a prioritized action plan. Five minutes to read, immediate clarity on where to focus.",
    ],
    "curveball_scope": [
        "The report covers security, SEO, ADA, email authentication, and conversion in depth — {finding_count} issues across all five areas. "
        "Competitor comparisons are benchmarked against SMB industry baselines. Custom scope is available as a follow-on engagement.",
        "This report focuses on what's directly impacting your site's performance and risk. Additional custom analysis (landing pages, ad copy) is a natural next step after you've addressed the priority items.",
        "The 30/60/90-day roadmap prioritizes the highest-ROI fixes first. Once the foundations are solid, the next conversation is about growth strategy.",
    ],
    "compliance_cautious": [
        "ADA findings come from WCAG 2.1 AA checks with element-level evidence — each violation includes the specific HTML element and failure summary. "
        "This report is informational; your attorney or developer should review before implementation. No legal advice is implied.",
        "Every finding includes page-level evidence and remediation steps your developer can verify directly before implementing changes.",
        "The scan covers {finding_count} items with documented evidence. Nothing in this report is a legal determination — it's a technical assessment your team can act on after appropriate review.",
    ],
    "refund_risk": [
        "Every finding includes a specific page URL and evidence snippet so you can independently verify each issue against the live site. "
        "The report is evidence-based, not opinion-based — {finding_count} documented items your developer can confirm.",
        "The value is in the evidence: page-level data, security header inspection, DNS record analysis. If your developer finds a finding doesn't apply, they'll know why within 30 seconds of checking.",
        "The roadmap prioritizes fixes by impact and effort. Even if a handful of items aren't relevant to your setup, the high/critical items alone typically justify the investment.",
    ],
    "timeline_pressure": [
        "The 0–30 day roadmap is built for immediate action. {hl0} has a complete remediation step your developer can implement today. "
        "High/critical items are scoped at 30 minutes to 4 hours each.",
        "Your developer can start on {hl0} today — the fix is documented in the report. Most high/critical items can go live within 24–48 hours of implementation.",
        "The report is ready now. Each 0–30 day item includes an effort estimate and skill requirement so you can assign the right person immediately.",
    ],
    "comparison_shopper": [
        "Unlike generic audit tools, this report includes real-browser screenshots, email authentication analysis, and a prioritized roadmap with effort estimates — "
        "things most agency audits skip. {hl0} is a finding most automated tools miss entirely.",
        "The differentiator is depth and evidence: {finding_count} findings, each with a specific page URL, severity, and actionable fix. "
        "Most audits give you a score. This gives you a fix list.",
        "This is an independent technical assessment, not a sales pitch for ongoing services. You get the full picture upfront — including {hl0} — so you can make an informed decision.",
    ],
    "repeat_skeptic": [
        "The findings are cross-validated: {hl0} appears across multiple independent checks with consistent evidence. "
        "Your developer can verify against the live site using the page URLs provided in under 5 minutes.",
        "Fair to push back. Here's the concrete case: {hl0} is documented with a specific page URL, HTTP response data, and a step-by-step fix. "
        "That level of specificity is what justifies the $299.",
        "The report surfaces {finding_count} issues with documented evidence. If even 3–4 high/critical items are valid and fixed, the ROI is clear — and most sites have many more than that.",
    ],
    "already_has_agency": [
        "This report surfaces issues agencies often deprioritize — specifically {hl0} and {hl1}. "
        "It's designed as an independent second opinion, not a replacement for your agency relationship.",
        "Most agencies focus on what they're billing for. This audit covers email authentication, security headers, and ADA compliance — "
        "areas that fall through the cracks in typical agency scopes. {hl0} is a good example.",
        "Think of it as an independent benchmark: {finding_count} findings your agency can use to reprioritize their backlog. "
        "Most clients find 3–5 items their agency wasn't aware of.",
    ],
    "data_privacy_concerned": [
        "This was a passive, read-only scan of publicly accessible pages only — the same view any browser or search engine crawler sees. "
        "No visitor data, backend access, form submissions, or private information was collected or stored.",
        "The scan operates like a browser visiting your site: it reads publicly visible HTML, checks security headers, and looks up DNS records. "
        "Nothing proprietary was accessed or retained.",
        "No authentication was bypassed, no data was stored server-side, and no visitor sessions were affected. "
        "The full methodology is documented in the report appendix for your review.",
    ],
    "overwhelmed_owner": [
        "The 0–30 day roadmap in the report does the prioritisation for you: three items, scoped at 30 minutes to 4 hours each, ranked by business impact. "
        "You don't need to tackle everything — just the top three this month.",
        "Start with {hl0} — it has the clearest remediation and the highest impact. "
        "Hand that single item to your developer today and you'll have tangible progress without touching the rest of the list yet.",
        "The report organises {finding_count} issues into three time windows: 0–30, 31–60, and 61–90 days. "
        "The 0–30 day bucket has the quick wins. Focus there first — everything else can wait.",
    ],
    "seo_focused_buyer": [
        "Security and ADA directly affect your rankings: Google's Core Web Vitals and HTTPS status are confirmed ranking signals, "
        "and page experience penalties reduce visibility. {hl0} is a ranking factor, not just a security issue.",
        "DMARC/SPF alignment affects whether Google indexes your business authoritatively and prevents spoofed domains from stealing your brand search traffic. "
        "These aren't separate from SEO — they're foundational to it.",
        "The report covers {finding_count} findings; the SEO section alone has the highest-leverage items for rankings: "
        "{hl0}, structured data gaps, and title/meta issues that affect click-through rates directly in SERPs.",
    ],
    "mobile_first_buyer": [
        "The scan captured real mobile screenshots at 390px viewport width and measured browser load time on a mobile session. "
        "{hl0} was detected on the mobile view — your phone visitors are experiencing this right now, not just desktop users.",
        "Mobile traffic is critical. The report includes real-browser mobile screenshots and documents {finding_count} issues "
        "that directly affect smartphone experience: viewport rendering, tap-target sizes, and page load performance on mobile connections.",
        "Mobile page speed and viewport configuration are confirmed Google ranking factors for mobile-first indexing. "
        "{hl0} creates friction specifically for phone users — the remediation is documented with estimated effort so your developer can ship it fast.",
    ],
    "accessibility_attorney": [
        "ADA findings in this report come from automated axe-core WCAG 2.1 AA checks with element-level evidence — "
        "each violation includes the HTML element, failure summary, and WCAG criterion reference. "
        "This is the same toolchain law firms use for initial demand-letter assessments.",
        "The report documents {finding_count} issues with page-level URLs and element-level HTML snippets. "
        "Your attorney can use the WCAG criterion citations to demonstrate which specific violations existed "
        "and what remediation was implemented — establishing good-faith remediation effort.",
        "Priority remediation order for legal exposure: (1) form labels and alt text — most common complaint basis, "
        "(2) keyboard navigation and skip links — WCAG 2.4.1 Level A, (3) color contrast and ARIA landmarks. "
        "The 0–30 day roadmap sequences exactly this prioritisation with effort estimates.",
    ],
    "performance_anxious": [
        "That low PageSpeed score is directly tied to findings in this report. "
        "{hl0} is one of the primary contributors — the remediation is documented with estimated effort "
        "so your developer can target the highest-impact fixes first and re-measure within a single sprint.",
        "A low PageSpeed score usually has 2–3 root causes rather than 20. "
        "The performance findings in this report identify the specific patterns — render-blocking scripts, "
        "missing preconnect hints, images without dimensions — that account for the majority of your score gap. "
        "Fix those {finding_count} items first before optimising anything else.",
        "PageSpeed score is a composite metric, but Google's Core Web Vitals (LCP, CLS, FID) are the confirmed ranking signals. "
        "This report flags the CWV contributors specifically: {hl0} is a direct LCP/CLS issue with a "
        "step-by-step fix your developer can implement today.",
    ],
    "roi_focused_buyer": [
        "The report's Revenue Recovery Potential section models three scenarios: conservative, base, and upside. "
        "At base, fixing {hl0} and the top 4 findings is projected to recover $1,500–$3,000/month in leads "
        "within 60 days — payback on $299 is typically under 30 days based on SMB conversion benchmarks.",
        "Here's the math: if your site converts 1% of visitors at an average customer value of $500, "
        "a 15% conversion improvement from fixing the documented issues adds roughly $750/month per 1,000 monthly visitors. "
        "The ROI model in the report shows your specific numbers based on finding severity.",
        "The report includes a commercial viability table: {finding_count} findings mapped to estimated revenue impact "
        "with low/base/upside scenarios. Most clients see payback in 30–45 days — the 0–30 day roadmap is "
        "specifically sequenced for fastest-to-implement, highest-revenue-impact items first.",
    ],
    "cybersecurity_worried": [
        "The email spoofing attack vector is documented in this report: your DMARC and SPF configuration "
        "gaps allow anyone to send email appearing to come from your domain. "
        "{hl0} is the highest-priority finding to block — the remediation includes the exact DNS records "
        "your IT contact needs to add, typically deployable in 30 minutes without any site downtime.",
        "The report documents {finding_count} security issues including email authentication gaps, exposed "
        "server paths, and missing browser security headers — each with page-level evidence and a specific fix. "
        "The 0–30 day roadmap leads with email authentication (SPF/DMARC/DKIM) because that directly "
        "prevents your domain from being weaponised in phishing attacks against your own customers.",
        "Phishing attacks exploiting your domain are possible because DMARC policy is missing or set to 'none'. "
        "The fix is a DNS TXT record: 'v=DMARC1; p=reject; rua=mailto:dmarc@yourdomain.com'. "
        "This is documented in the report with the complete record value and validation steps. "
        "The SPF record update is equally straightforward — your IT team can ship both in one session.",
    ],
    "franchise_owner": [
        "The report produces independently verifiable evidence for each finding — page URL, severity rating, "
        "and a specific remediation step. {hl0} is documented in a format your corporate IT team and "
        "brand standards team can review directly. The 30/60/90-day roadmap maps to project scope for "
        "budget approval requests.",
        "For franchise compliance documentation, the ADA and security findings are the most relevant. "
        "Each finding references the specific WCAG success criterion or OWASP category, which are the "
        "standard formats corporate compliance and legal teams recognise. {finding_count} documented "
        "issues with effort estimates gives you a concrete project plan to submit for corporate sign-off.",
        "The report format is designed to support vendor conversations: every finding includes the specific "
        "page URL, technical evidence, and a remediation step your approved IT vendor can scope and implement. "
        "The 30/60/90-day roadmap with effort estimates maps directly to project milestones your franchise "
        "development team can track against brand standards requirements.",
    ],
    "quick_start_buyer": [
        "Your 48-hour action list: (1) {hl0} — remediation is documented, dev effort estimated at 30–60 min. "
        "(2) {hl1} — a DNS/configuration fix your IT contact can ship without a code deploy. "
        "That's the minimum viable starting point. Everything else can follow next week.",
        "Two things for this week: {hl0} and {hl1}. Both are in the 0–30 day bucket with step-by-step remediation. "
        "Hand the PDF to your developer today — the fix is described precisely enough to implement without a discovery call.",
        "The 0–30 day roadmap has exactly what you need: top items sorted by effort (smallest first) with who does each task. "
        "{hl0} can go live today. {hl1} is a 30-minute DNS update. Start there — the rest of the list can wait 2–3 weeks.",
    ],
    "healthcare_compliance_buyer": [
        "The report documents {finding_count} findings with page-level evidence — including contact form security "
        "(HTTPS submission, field exposure), ADA/WCAG 2.1 AA violations with element-level detail, and email authentication "
        "gaps. The ADA section maps directly to WCAG success criteria your compliance counsel can reference. "
        "{hl0} is documented with the specific HTML element and failure summary.",
        "HIPAA applies to PHI in transit and at rest — the report covers form submission security (HTTPS endpoints, "
        "encryption headers) and cookie handling. No PHI is collected by the scan itself; this is a passive technical "
        "audit. The remediation steps are documented so your IT vendor can implement before your next compliance review.",
        "The ADA findings in this report include WCAG criterion references and element-level evidence that your "
        "attorney or compliance team can use to demonstrate good-faith remediation effort. {hl0} is documented at "
        "the code level. The 30/60/90-day roadmap gives you a defensible timeline with effort estimates by role.",
    ],
    "ecommerce_cro_owner": [
        "The report flags {finding_count} issues directly affecting checkout conversion: {hl0} increases form friction, "
        "load time findings show pages exceeding 3s thresholds (Google's abandonment threshold), and missing trust "
        "signals — testimonials, secure badge visibility, click-to-call — all reduce purchase confidence at decision moments.",
        "The performance section documents exact browser load times for key pages — not just technical scores but "
        "real-user-experience data. {hl0} is one of the top abandonment drivers: 1 in 5 cart sessions drops off when "
        "checkout exceeds 3 seconds. The remediation is a step-by-step CDN + compression fix your dev can ship today.",
        "The conversion section identifies form field friction ({hl0}), missing social proof on product pages, "
        "and absent trust signals at the point of purchase. The 0–30 day roadmap prioritizes the two fixes with "
        "the highest expected lift on checkout completion rate — both are documented with effort and skill estimates.",
    ],
    "social_proof_seeker": [
        "Every finding in this report is tied to live, verifiable evidence: the specific page URL, HTTP response data, "
        "and in many cases a browser screenshot. {hl0} is directly visible in the page source — your developer can "
        "confirm it in 60 seconds. The methodology is consistent with WCAG 2.1, OWASP top-10, and Google's Core Web Vitals standards.",
        "The scan process is transparent: HTTP headers are inspected directly, DNS records are queried live, and ADA "
        "violations are captured via automated WCAG testing. {hl0} is verifiable against the live site right now. "
        "The report includes the evidence snippet alongside each finding so you can cross-check independently.",
        "The {finding_count} findings are drawn from publicly observable signals — nothing proprietary or blackbox. "
        "For example, {hl0} is detected via a standard HTTP header check any developer can reproduce in a terminal. "
        "The 30/60/90-day roadmap lists effort estimates and role assignments so you can validate reasonableness with your team.",
    ],
    "enterprise_it_manager": [
        "Each security finding is assigned a severity level (critical/high/medium/low) mapped to OWASP category and "
        "CVSS impact class. {hl0} is documented with the specific page URL, HTTP evidence, and a concrete remediation "
        "step — compatible with your vulnerability management workflow. The report is structured for technical review.",
        "The report covers {finding_count} findings across security, email authentication (SPF/DKIM/DMARC), ADA/WCAG, "
        "SEO, and performance. Each finding includes page URL, severity, evidence, and remediation. "
        "High/critical items are flagged for immediate action; medium items include effort estimates for sprint planning. "
        "{hl0} is in the high-priority queue with a 2–4h remediation estimate.",
        "Security findings reference the applicable OWASP Top 10 category or WCAG success criterion where applicable. "
        "{hl0} aligns with OWASP A05 (Security Misconfiguration) and is documented with sufficient detail for a "
        "CVE-style internal ticket. The report JSON is also available for ingestion into your tracking system if needed.",
    ],
    "budget_constrained_nonprofit": [
        "The report prioritises fixes that are free or low-cost first — plugin installs, DNS record updates, "
        "and configuration changes that your team can implement without a paid developer. {hl0} is one of those: "
        "it takes under 30 minutes to fix and costs nothing beyond an afternoon of staff time.",
        "For nonprofits, ADA compliance is the highest-risk category: DOJ enforcement letters typically cite WCAG 2.1 AA violations "
        "with a remediation deadline. The {finding_count} accessibility findings in this report include WCAG criterion references "
        "so you have documentation of good-faith remediation effort if needed. The remediation steps use free tools.",
        "Almost every high-priority fix in the report uses either a free WordPress plugin, a free DNS record update, "
        "or a no-cost configuration change. {hl0} alone is a 15-minute fix. You get the full actionable list — "
        "then decide which you can DIY and which need a developer for the more complex items.",
    ],
    "multi_location_owner": [
        "The report is scoped to the primary website URL, which is typically the highest-risk entry point. "
        "The {finding_count} findings cover the most impactful cross-site issues — many apply universally across "
        "all location pages since they share the same platform, DNS setup, and security configuration. "
        "{hl0} is one example that affects every location equally.",
        "For multi-location businesses, local SEO issues compound across locations: duplicate meta descriptions, "
        "missing LocalBusiness schema, and inconsistent NAP (name/address/phone) data hurt every location's "
        "local ranking independently. This report identifies those cross-site patterns so your developer can "
        "fix them systematically rather than location by location.",
        "The priority matrix in the report separates site-wide fixes (affect all locations — highest leverage) "
        "from location-specific issues. {hl0} is a site-wide issue: fix it once in your CMS and every location "
        "page benefits immediately. That's the highest ROI starting point for a multi-location operation.",
    ],
    "local_seo_buyer": [
        "The report documents {finding_count} findings including schema markup, LocalBusiness structured data, "
        "and NAP-related issues that directly affect your local map pack ranking. {hl0} is one of the "
        "highest-leverage fixes for 'near me' visibility — it tells Google exactly who you are and where.",
        "Local pack ranking is driven by relevance, distance, and prominence. This report identifies the "
        "technical signals you're missing — missing schema, unlinked phone numbers, no review markup — "
        "that reduce your relevance score. {hl0} is an immediately fixable relevance signal your competitors "
        "likely already have in place.",
        "The SEO section breaks down which findings directly affect local search visibility: missing "
        "LocalBusiness schema, review aggregation markup, and NAP consistency issues all appear in the "
        "30-day roadmap. Your developer can implement the schema fix in under an hour using free JSON-LD "
        "and validate it with Google's Rich Results Test before it goes live.",
    ],
    "gdpr_anxious_buyer": [
        "The scan was read-only — we fetched publicly visible page content and HTTP headers, "
        "the same data Google's crawler accesses. No visitor data was collected, no sessions were tracked, "
        "and no backend systems were accessed. {hl0} was identified from your public homepage content.",
        "The report flags your cookie consent and data handling posture as part of the conversion findings. "
        "CCPA and GDPR both require a visible consent mechanism before tracking — the report documents "
        "whether a consent banner was detected and whether your contact form collects data without disclosure. "
        "These are the highest-priority compliance items for {finding_count} identified issues.",
        "The privacy and data handling findings include specific remediation steps: implementing a free "
        "CookieYes or OneTrust banner, adding a privacy policy link to your contact form, and updating "
        "your terms. These are not legal opinions — they are the technical conditions that trigger "
        "regulatory scrutiny and that you can resolve before an inquiry occurs.",
    ],
    "restaurant_owner": [
        "For restaurants, {hl0} directly connects to table bookings and 'near me' reservation searches. "
        "The report maps out the {finding_count} issues affecting your Google Maps visibility and online "
        "ordering conversion — schema markup, review rating display, and mobile load speed for your menu page.",
        "Google Maps ranking for restaurants is driven by relevance signals: LocalBusiness schema with "
        "cuisine type, menu URL, and opening hours, plus AggregateRating markup for star display in search. "
        "{hl0} is one of the missing signals that your competitors on the local 3-pack likely already have "
        "implemented. The fix is under 30 minutes with a free JSON-LD block.",
        "The conversion section specifically covers the issues that cost you reservations: slow menu page "
        "load time, missing click-to-call button, and no booking CTA above the fold. Each of these reduces "
        "the chance a mobile user completes a reservation rather than clicking back to a competitor. "
        "The {finding_count} findings include exact remediation steps for each.",
    ],
    "legal_professional": [
        "Law firm sites face unique risks: {hl0} is a compliance concern that touches both bar association "
        "advertising rules (which restrict certain claims in attorney marketing) and ADA exposure — DOJ "
        "settlement targets increasingly include law firm websites. The {finding_count} findings document "
        "the specific violations with WCAG criteria references for your ethics/compliance review.",
        "The contact form security findings are particularly relevant for legal practices: forms collecting "
        "client name, matter type, or case details without HTTPS submission or privacy disclosure create "
        "confidentiality risk. {hl0} is one of the security items that your general counsel or ethics "
        "advisor will want to review before the form goes live with prospective client data.",
        "ADA compliance for law firms is not optional — the DOJ has specifically cited legal websites in "
        "enforcement actions. The {finding_count} accessibility findings include WCAG 2.1 AA criterion "
        "references (1.3.1, 2.4.4, 4.1.2) that map directly to what plaintiffs' attorneys cite in demand "
        "letters. This documentation supports a good-faith remediation defense.",
    ],
    "referral_partner": [
        "Your advisor sent you here for a reason. The {finding_count} findings are verifiable by any "
        "developer — each one includes the exact page URL, the specific element or header, and a "
        "remediation step. {hl0} is something your developer can cross-check in 60 seconds. "
        "This is the opposite of generic agency advice — it's auditable evidence.",
        "Being burned before is the right reason to demand proof. Every finding comes with a page URL "
        "and the specific evidence extracted live from your site. The ROI is concrete: $299 vs the "
        "$1,500–$3,000 an agency would charge for a comparable audit — and you own the deliverable "
        "permanently, no monthly retainer required.",
        "I'll give you a reference: every finding links back to a publicly verifiable standard — "
        "WCAG 2.1 for accessibility, OWASP for security, Google's Core Web Vitals guidelines for "
        "performance. Your advisor or developer can independently verify every high-priority item "
        "in {finding_count} findings without trusting our word for it.",
    ],
    "review_reputation_buyer": [
        "The review situation is directly connected to {hl0} — without structured AggregateRating "
        "schema markup on your site, Google cannot display your star rating in organic search results "
        "even when you have existing reviews. That means competitors who implement review schema "
        "appear more trustworthy in SERPs, capturing clicks before prospects even reach Google Maps.",
        "Star ratings in search snippets increase CTR by 15–30% according to multiple SEO studies. "
        "The {finding_count} findings include schema and local SEO items that directly affect how "
        "your business appears across Google Search, Maps, and the local 3-pack — including whether "
        "your review count and rating appear next to your business name in results.",
        "Your Google Business Profile and your website schema need to be consistent — NAP alignment "
        "(Name, Address, Phone) and structured review markup signal trustworthiness to Google's "
        "local ranking algorithm. The report covers {hl0} and identifies the specific schema gaps "
        "that prevent your ratings from showing in organic results, not just Maps.",
    ],
    "b2b_saas_founder": [
        "Enterprise buyers run security and compliance checks before signing. {hl0} directly affects "
        "how your site scores on tools like SecurityHeaders.com and SSL Labs — and many enterprise "
        "security teams check those scores as part of vendor vetting. The report identifies the "
        "exact headers and TLS configuration gaps that would flag in an enterprise risk assessment.",
        "The {finding_count} findings include specific GDPR-relevant items: cookie consent implementation, "
        "form data handling, and CORS misconfiguration. For a SaaS selling to EU customers or "
        "enterprise procurement teams, these are now table-stakes — not optional. The report gives "
        "your team the exact items to remediate before your next enterprise deal review.",
        "Trust signals on your free-trial signup page directly affect enterprise conversion. "
        "The report covers {hl0} and includes ADA compliance gaps that Fortune 500 procurement "
        "teams increasingly include in their vendor accessibility reviews. SOC 2 readiness "
        "documentation also benefits from having a third-party web presence risk assessment on file.",
    ],
    "home_services_owner": [
        "When someone searches 'plumber near me' or 'emergency HVAC repair', Google's algorithm "
        "checks whether your website matches what your Google Business Profile says, whether your "
        "phone number is click-to-call, and how fast the page loads on a phone. {hl0} is exactly "
        "the kind of issue that suppresses local pack ranking — and the report shows exactly how to fix it.",
        "Your competitors who rank above you in the local 3-pack have likely fixed {finding_count} "
        "of the same issues this report identifies. The scan looks at LocalBusiness schema, NAP "
        "consistency, mobile performance, and your contact/call CTA — all factors Google's local "
        "ranking algorithm weights heavily for service-area businesses.",
        "You don't need to understand the technical details — the report's 30-day roadmap is written "
        "for non-technical owners and separates what you can fix yourself in your CMS from what "
        "your web person needs to handle. Most owners find 3–4 quick wins they can implement "
        "themselves the same day, without hiring anyone.",
    ],
    "dental_practice_owner": [
        "Patient acquisition from Google depends on a very specific set of signals: LocalBusiness schema "
        "with office hours and accepted insurance, HIPAA-aware form handling, ADA-compliant appointment "
        "booking flow, and fast mobile load times. {hl0} is one of the issues most likely affecting "
        "your visibility in 'dentist near me' searches right now.",
        "The report flags {finding_count} issues including form handling gaps and ADA barriers that affect "
        "elderly and mobility-impaired patients. For a dental or medical practice, ADA compliance is not "
        "optional — a single demand letter can cost $5,000–$25,000. The report gives you the exact WCAG "
        "violations to hand to your developer before that risk materializes.",
        "Privacy in healthcare is more scrutinized than most industries. Your contact and appointment "
        "forms were checked for privacy policy disclosure, HTTPS form submission, and data handling signals. "
        "{hl0} stands out as an immediate priority. The 30-day roadmap separates what your front-desk "
        "staff can fix in your website builder from what requires a developer.",
    ],
    "fitness_studio_owner": [
        "Your class booking conversion depends entirely on mobile UX — 80%+ of fitness searches happen "
        "on phones. {hl0} is creating friction at the exact moment a prospect decides to book. "
        "The report documents the specific page-level issues affecting your mobile booking funnel.",
        "Studios that outrank you locally have better LocalBusiness schema, faster mobile load times, "
        "and cleaner class-booking CTAs. The report identifies {finding_count} specific items across "
        "performance, conversion, and local SEO — including load time measurements from a real mobile "
        "browser simulation — so your designer knows exactly what to fix before next month.",
        "Instagram drives awareness, but Google converts it. {hl0} means that when a prospect clicks "
        "your Google Maps link or searches your studio name, the landing experience is losing them. "
        "The 30-day roadmap prioritizes the changes with the highest booking-rate impact first.",
    ],
    "print_media_traditionalist": [
        "I understand — you built your business before Google existed. But here's the reality: "
        "{finding_count} specific issues were found that affect how often you show up when someone "
        "searches for your type of business in your area. That translates directly to phone calls "
        "and walk-ins you're not getting right now.",
        "No jargon, just outcomes: {hl0} means customers who Google your business category in your "
        "zip code see a competitor instead of you. This report gives your developer or nephew/niece "
        "a specific list of what to fix so that stops happening.",
        "Yellow Pages worked because it guaranteed visibility to ready buyers. Google has replaced "
        "that role — and the report tells you exactly why your current site isn't showing up for "
        "those same ready-to-buy searches, with {finding_count} specific fixable reasons.",
    ],
    "first_time_website_owner": [
        "No technical knowledge required. The report explains each issue in plain language, "
        "groups fixes by who does what (you vs. your web builder vs. a developer), and gives "
        "you a 0–30 day list of the 3 most important things to tackle first.",
        "Think of it as a website health check-up. {hl0} is like a doctor saying 'your blood "
        "pressure is a bit high — here's the one medication that fixes it.' The report gives "
        "you that same kind of clear, actionable diagnosis across {finding_count} areas.",
        "The first thing to know: you don't need to understand everything in the report to act "
        "on it. The 0–30 day roadmap tells you exactly what to hand to your web designer. "
        "Most first-timers fix the top 3 items in a weekend. That's all it takes to start "
        "showing up better in Google and looking more trustworthy to visitors.",
    ],
    "budget_approval_needed": [
        "The report includes an ROI section with specific payback period calculations. For a "
        "business your size, the base-case scenario shows full payback within 45 days based on "
        "{hl0} and {hl1} improvements. The executive summary is written in plain language "
        "for non-technical reviewers — it's designed to be presented internally without translation.",
        "Approval processes make sense for spend at this level. The report comes with a 1-page "
        "executive summary that summarises the {finding_count} identified risks, estimated revenue "
        "impact, and 30-day payback framing — structured specifically so you can share it with "
        "finance or leadership without needing to walk them through the technical details.",
        "Here's what we see in approval scenarios: the most compelling framing for a CFO is "
        "competitive risk, not technical debt. '{hl0}' means a competitor who has fixed this issue "
        "is capturing search traffic and calls that should be yours. The report quantifies that "
        "gap in plain dollar terms — that's the language that moves approval committees.",
    ],
    "already_has_seo_agency": [
        "SEO agencies focus on keyword rankings, backlinks, and content strategy — that's their "
        "core scope. This report covers the 4 areas most SEO retainers explicitly exclude: "
        "security posture (TLS, security headers, exposed files), email authentication (SPF/DKIM/DMARC "
        "— spoofing risk), ADA/accessibility compliance (active legal exposure), and conversion UX. "
        "{hl0} is a perfect example — almost no SEO agency checks for that.",
        "Think of it as complementary, not competing. Your agency is optimizing to attract more "
        "visitors. This report identifies why visitors who arrive on your site don't convert — "
        "form friction, weak CTAs, trust signal gaps, and page speed issues. {finding_count} "
        "specific items were found that would not appear in a standard SEO audit.",
        "Many of our clients have active SEO agencies when they receive this report. The findings "
        "actually help the agency: fixing {hl0} removes a technical barrier that was undermining "
        "the agency's ranking work. Agencies tend to welcome the supplementary audit because it "
        "removes the 'why isn't this ranking despite our efforts' mystery.",
    ],
    "insurance_agent_owner": [
        "For an insurance agency, {hl0} is a direct E&O exposure risk. "
        "If a prospect submits a quote request through an insecure form and that data is compromised, "
        "your agency faces regulatory scrutiny and potential client lawsuits — regardless of whether "
        "you processed the data or a third-party service did. The report identifies exactly which "
        "forms and email authentication gaps need to be closed before they become liability events.",
        "Email authentication (SPF, DKIM, DMARC) is especially critical for insurance agencies. "
        "Your domain is used to send policy quotes, renewal notices, and claim updates — all of which "
        "clients act on financially. Without proper DMARC enforcement, a criminal can spoof your "
        "exact email address to send fraudulent quotes or phishing links, and clients will believe "
        "it came from you. {hl0} is documented with the exact DNS fix required.",
        "ADA compliance for an insurance agency isn't optional — your site is a public accommodation "
        "under DOJ enforcement guidance, and elderly and disabled clients depend on your website to "
        "access policy information and file claims. {finding_count} accessibility findings were "
        "documented with specific page URLs and WCAG criteria. This is your pre-litigation checklist.",
    ],
    "childcare_provider_owner": [
        "Parents searching 'daycare near me' on their phones need to find you — and then trust you "
        "immediately. {hl0} directly affects that first impression. The report documents specific "
        "mobile performance and local SEO issues with exact page URLs and remediation steps so your "
        "enrollment inquiries don't drop while a competitor with a faster site gets the call.",
        "Enrollment form security is not just a technical issue for a childcare provider — it's a "
        "trust signal. Parents who see security warnings or fill out forms on insecure pages will "
        "choose a competitor. {hl0} includes the specific fix to protect form submissions and "
        "eliminate the browser warning that's costing you enrollment inquiries.",
        "The report found {finding_count} issues affecting your site's visibility and trustworthiness "
        "to parents. Local schema markup, Google Maps optimization, and ADA compliance for parents "
        "with disabilities are all documented — these are the exact signals that determine whether "
        "your site appears in the local 3-pack when parents in your zip code search for childcare.",
    ],
    "physical_therapist_owner": [
        "For a physical therapy practice, ADA compliance isn't optional — it's a direct patient "
        "care responsibility. Many of your patients have mobility limitations, low vision, or "
        "cognitive barriers that require an accessible website to book appointments independently. "
        "{hl0} is documented with the exact WCAG criterion violated, page URL, and the specific "
        "fix your web developer or CMS can implement to bring the booking flow into compliance.",
        "Patient intake form security is the top concern we see for PT clinics. {hl0} identifies "
        "exactly how your online forms are transmitting data and whether they meet basic security "
        "standards for health-adjacent contact information. The fix is documented step-by-step — "
        "your hosting provider or web developer can implement it without requiring a full site rebuild.",
        "The report found {finding_count} issues across security, ADA compliance, and local search — "
        "the three areas that most directly affect new patient acquisition for a PT practice. "
        "Fixing the top ADA items first protects your practice from accessibility complaints, "
        "while the local SEO items position you to rank for 'physical therapy near me' searches "
        "that drive referral-independent patient inquiries.",
    ],
    "auto_repair_shop_owner": [
        "For an auto repair shop, Google reviews and 'near me' visibility drive almost every new "
        "customer call. {hl0} directly affects how your shop appears in local search results. "
        "The report includes {finding_count} specific issues — each with an exact fix — that when "
        "resolved will improve your Google Maps presence and make it easier for drivers to call you.",
        "The most important thing to know: most of these fixes don't require a developer. "
        "{hl0} is the kind of fix your web builder or shop manager can handle in under an hour. "
        "The report separates 'do this yourself this week' from 'ask your developer' items so "
        "you're not paying someone $150/hour for something that takes 10 minutes in a CMS.",
        "Bottom line: a competitor shop with better local SEO and more visible click-to-call "
        "buttons is getting calls that should come to you. {hl0} is documented with the exact "
        "page URL and the specific change that improves local pack visibility. The whole point "
        "is to get more phone calls. That's what this report is built around.",
    ],
    "accountant_practice_owner": [
        "For a CPA or bookkeeping firm, your reputation is your business — and {hl0} represents "
        "a direct risk to client trust and professional standing. When sensitive financial data "
        "flows through an insecure contact form or your domain can be used to send fraudulent "
        "tax notices, the consequence isn't just a technical issue — it's a malpractice exposure "
        "and a client relationship problem. The report documents {finding_count} specific risks "
        "with remediation steps that protect your practice and your clients.",
        "Your clients trust you with their most sensitive financial information. {hl0} is exactly "
        "the kind of security gap that sophisticated clients — especially business owners and "
        "high-net-worth individuals — will notice and question if it results in an incident. "
        "The report separates urgent security fixes from SEO and conversion improvements, so your "
        "IT contact or web developer can prioritize the items that protect client data first.",
        "The email authentication section is especially critical for accounting practices: {hl0} "
        "means your domain can currently be used to send fake tax notices, IRS impersonation "
        "emails, or fraudulent payment requests that appear to come from your firm. The fix is "
        "a DNS record change that takes 10 minutes and completely closes that spoofing risk.",
    ],
    "veterinary_clinic_owner": [
        "For a veterinary clinic, your website is often the first touchpoint when a pet owner "
        "is in an urgent situation — their dog is limping at 9pm and they're searching 'vet near me' "
        "on their phone. {hl0} directly affects whether your clinic appears in those moments "
        "and whether a panicked owner can immediately tap to call. The report covers {finding_count} "
        "specific issues across mobile experience, local SEO, and conversion — all tied to call volume.",
        "Google Maps ranking for 'emergency vet near me' and 'vet near me' is determined partly "
        "by signals our scan measures: {hl0} is one of the factors that affects your local pack "
        "visibility. The report also flags review schema — if your Google star ratings aren't "
        "showing in search results, that's a trust signal gap that costs you new client calls "
        "compared to competitors whose ratings appear directly in the search listing.",
        "Mobile experience is the highest-priority issue for veterinary practices: most pet owners "
        "searching for emergency care are on their phones with adrenaline running. {hl0} affects "
        "how fast your clinic information loads and whether a pet owner can tap to call with one "
        "touch. The mobile audit section of the report lists every barrier between a mobile search "
        "and a successfully placed call to your front desk.",
    ],
    "property_management_owner": [
        "For a property management company, your website is the front door for prospective tenants "
        "and property owners searching 'property management near me' or '[city] apartments for rent'. "
        "{hl0} is one of the findings that directly affects your local search visibility for those "
        "high-intent searches. The report covers {finding_count} issues across SEO, lead form "
        "security, ADA, and conversion — all mapped to the tenant acquisition pipeline.",
        "Fair Housing Act ADA obligations apply to your online listing pages — inaccessible "
        "forms or missing screen-reader support can be cited in discrimination complaints. "
        "{hl0} is the highest-priority accessibility item in your report, documented with the "
        "exact page URL and a plain-English fix that takes under 20 minutes to implement. "
        "The report flags the full ADA picture so you can remediate before a complaint arises.",
        "Tenant application forms collect some of the most sensitive personal data a business "
        "touches — SSNs, income documents, references. {hl0} is a security finding in the report "
        "that affects how that data is handled in transit. The fix is straightforward, takes no "
        "coding, and gives prospects the confidence that their application data is protected.",
    ],
    "nonprofit_board_member": [
        "WCAG 2.1 AA accessibility has become a grant eligibility requirement at several major "
        "foundations — and {hl0} is one of the ADA findings in your report that would be flagged "
        "in a funder's accessibility review. The report covers {finding_count} issues across "
        "ADA, email security, and donor conversion, with every fix categorized as free/no-code, "
        "plugin install, or developer work — so your volunteer IT team can triage immediately.",
        "Fraudulent fundraising emails impersonating nonprofits are one of the most common "
        "donor scams. {hl0} in the email authentication section means your domain can currently "
        "be used to send fake donation solicitations that appear to come from your organization. "
        "The fix is a DNS record change that takes 10 minutes and protects your donor base — "
        "and it's completely free to implement through your existing domain registrar.",
        "Donor trust signals on your donation page directly affect conversion rates — "
        "and {hl0} is one of the conversion findings that affects whether a first-time donor "
        "completes their gift or abandons mid-form. The report separates zero-cost fixes from "
        "items requiring a developer, so you can take the free wins to your next board meeting "
        "and present a concrete plan for the remainder.",
    ],
    "tutoring_center_owner": [
        "Enrollment inquiries start with a parent searching 'SAT prep near me' on their phone — "
        "and {hl0} is one of the findings that reduces your visibility in those exact searches. "
        "The report covers {finding_count} issues including local SEO, mobile load speed, and "
        "inquiry form security, with every finding linked to its specific page URL and evidence. "
        "Your developer can start on the high-priority items the same day they receive this report.",
        "Parents researching tutoring centers check Google reviews and star ratings before calling. "
        "{hl0} is the review schema finding that shows your Google listing isn't displaying star "
        "ratings in search results — which directly affects how many parents choose to call you "
        "versus a competitor whose profile shows ratings. This is a free, 20-minute JSON-LD fix. "
        "The report also covers student form data security and mobile click-to-call friction.",
        "When a parent fills out your enrollment inquiry form on their phone, {hl0} means the "
        "data submitted could be intercepted in transit — a serious concern when collecting a "
        "child's name, grade, and parent contact information. The report covers {finding_count} "
        "issues ranked by enrollment impact, with free-fix and developer items clearly separated "
        "so you can take immediate action without waiting for your next budget cycle.",
    ],
    "boutique_hotel_owner": [
        "Every booking that goes through Expedia or Booking.com costs you 15–25% in commission. "
        "{hl0} is one of the conversion findings that explains why guests are choosing OTAs over "
        "booking directly — and it's a fixable page-level issue, not a platform problem. "
        "The report covers {finding_count} friction points including above-fold booking CTAs, "
        "page load speed for image-heavy gallery pages, and review schema for Google Hotel Pack.",
        "Your Google star ratings determine whether a leisure traveler clicks your direct site "
        "or a competitor's listing. {hl0} is the review schema finding showing your property "
        "isn't eligible for Google Hotel Pack star ratings — a free JSON-LD fix that can be "
        "live within 48 hours. The report also covers mobile speed (critical for the 60%+ of "
        "travel searches that start on phones) and conversion friction on your booking page.",
        "Mobile performance is where most boutique hotels lose direct bookings — "
        "a guest browsing options on their phone will abandon a slow gallery page and book "
        "through an OTA instead. {hl0} is one of the performance findings affecting your "
        "mobile load time. The report covers image optimization, above-fold CTA clarity, "
        "and review trust signals — all framed in terms of direct booking conversion.",
    ],
    "photography_studio_owner": [
        "Photography clients book based on trust and first impressions — and {hl0} is one of "
        "the findings affecting how quickly your portfolio loads on the phones potential clients "
        "use to browse. The report covers {finding_count} issues including image performance, "
        "booking form security, and how Google Images SEO can drive free organic traffic "
        "to your portfolio from 'photographer near me' searches.",
        "Your booking inquiry form collects client name, event date, location, and budget — "
        "and {hl0} is a finding showing that data transmission has a security gap. The report "
        "covers {finding_count} issues in plain English: which gallery images are slowing "
        "your mobile page, which SEO gaps let competitors outrank you, and which security "
        "fixes protect your client inquiries — with clearly separated free fixes and dev items.",
        "Portfolio image load time is the number-one conversion killer for photographers — "
        "a slow gallery tells a prospective client 'this studio doesn't take their website "
        "seriously.' {hl0} is the performance finding explaining the root cause. The report "
        "covers {finding_count} issues including WebP image optimization, schema markup for "
        "Google Images, and social proof signals that match how clients search for photographers.",
    ],
    "financial_advisor_owner": [
        "Your domain being spoofed in phishing emails is a direct liability risk for an "
        "advisory practice — a client who gets a fake invoice from 'you' asking for a wire "
        "transfer can cause irreparable trust damage. {hl0} is the email authentication "
        "finding that explains your current exposure. The report covers {finding_count} issues "
        "including DMARC, SPF, form security for financial data, and ADA for elderly clients.",
        "FINRA has cited inadequate website security as a supervisory control failure in "
        "recent enforcement actions. {hl0} is a finding that maps to regulatory risk your "
        "compliance officer needs to know about. The report covers {finding_count} issues "
        "ranked by liability impact: email spoofing prevention, secure form transmission, "
        "ADA compliance for low-vision clients, and SEO gaps that affect referral credibility.",
        "Elderly and low-vision clients represent a significant portion of most advisory "
        "practices — and {hl0} is an accessibility finding that could prevent them from "
        "completing a contact or onboarding form. ADA Title III has been applied to financial "
        "services websites in recent DOJ enforcement. The report covers {finding_count} issues "
        "with remediation steps framed for both compliance documentation and client experience.",
    ],
    "optometry_practice_owner": [
        "Your patients trust you with their vision — and {hl0} is a finding showing that your "
        "website may not extend that same level of care to older patients who need accessible "
        "digital tools. The report covers {finding_count} issues including ADA compliance for "
        "low-vision users, appointment form security for vision insurance data, and local SEO "
        "for 'eye exam near me' searches that drive new patient acquisition.",
        "HIPAA-adjacent privacy risk from online vision insurance inquiry forms is a real "
        "exposure for eye care practices — {hl0} is a security finding that touches your "
        "patient intake process. The report covers {finding_count} issues ranked by patient "
        "trust and local search impact: form data security, ADA accessibility for elderly "
        "patients, click-to-call for phone-preferring patients, and review schema for Google "
        "local pack visibility.",
        "Optometry is a highly local business — most new patients find you through 'optometrist "
        "near me' searches and then check reviews before booking. {hl0} is an SEO finding "
        "affecting how visible your practice is to nearby searchers. The report covers "
        "{finding_count} issues including local schema, review markup, and the specific "
        "technical gaps that let competing practices outrank you in the local pack.",
    ],
    "landscaping_business_owner": [
        "Your competitors are showing up above you in Google Maps for 'landscaping near me' — "
        "and {hl0} is a local SEO finding that explains part of why. The report covers "
        "{finding_count} issues in plain language: which technical gaps are costing you "
        "visibility in local search, why your phone number may not be click-to-call on mobile, "
        "and how missing review schema prevents your star ratings from showing in search results.",
        "Homeowners search for landscaping services from their phones while literally standing "
        "in their yard — {hl0} is a mobile performance finding affecting how your site loads "
        "for those searchers. The report covers {finding_count} issues including mobile speed, "
        "click-to-call, above-fold quote CTAs, and the local SEO signals that determine whether "
        "they call you or a competitor who appears above you in the local 3-pack.",
        "You've built {finding_count} issues' worth of real, fixable advantages over where you "
        "are today — and {hl0} is the finding with the most direct link to whether a potential "
        "customer picks up the phone. The report separates free fixes from developer work, "
        "with plain-English explanations of what each issue means for seasonal booking volume "
        "and why Google is currently sending your ideal customers to competitors first.",
    ],
    "wedding_venue_owner": [
        "Brides spend less than 8 seconds deciding whether a venue website is worth exploring — "
        "and {hl0} is a performance finding that may be losing you inquiries in those first "
        "seconds. The report covers {finding_count} issues including gallery page load speed, "
        "above-fold booking CTA clarity, review schema for Google local pack star ratings, and "
        "the local SEO gaps that let newer venues outrank you despite having fewer bookings.",
        "Wedding venue discovery happens almost entirely on mobile — brides are pinning ideas, "
        "texting links to partners, and checking galleries from their phones. {hl0} is a "
        "mobile experience finding that affects whether those critical first-impression moments "
        "convert into inquiry form submissions. The report covers {finding_count} issues "
        "ranked by inquiry-conversion impact with specific fixes your web designer can "
        "implement before peak January–March inquiry season.",
        "Every booking lost to a competitor venue that ranks above you in 'wedding venues near me' "
        "represents several thousand dollars in revenue. {hl0} is a finding with a direct "
        "connection to your local search ranking. The report covers {finding_count} issues "
        "including schema markup for star ratings, gallery performance, booking form friction, "
        "and the specific SEO signals that Google uses to rank venues in local searches — "
        "all explained without jargon, with clear effort estimates for each fix.",
    ],
    "e_learning_platform_owner": [
        "If your course platform serves students in the EU or California, {hl0} is a compliance "
        "finding that creates real legal exposure — GDPR Article 5 and CCPA both apply to "
        "online enrollment data. The report covers {finding_count} issues including cookie "
        "consent implementation, contact/enrollment form data handling, email authentication "
        "to prevent domain spoofing, and WCAG accessibility for students with disabilities "
        "who depend on screen readers to take your courses.",
        "Online course platforms attract students with disabilities — screen reader users, "
        "students with motor impairments using keyboard-only navigation, and low-vision learners. "
        "{hl0} is an accessibility finding that may be excluding students from enrolling or "
        "completing your courses. The report covers {finding_count} issues including WCAG 2.1 AA "
        "compliance gaps, form accessibility for enrollment and checkout, and video caption "
        "requirements — all with specific remediation steps your developer can implement.",
        "Course discovery starts with Google — and {hl0} is a finding affecting how visible "
        "your platform is for '[subject] online course' searches. The report covers {finding_count} "
        "issues including technical SEO for course pages, schema markup for course structured data, "
        "page load performance for video-heavy content, enrollment form security, and the "
        "GDPR/CCPA compliance gaps that could create liability with your global student base.",
    ],
    "chiropractor_practice_owner": [
        "For a chiropractic practice, new patients come from two places: referrals and Google. "
        "{hl0} is a local SEO finding that directly affects whether patients searching "
        "'chiropractor near me' or 'back pain [your city]' see your practice in the local 3-pack. "
        "The report covers {finding_count} issues including local schema, review markup for "
        "Google star ratings, click-to-call for patients in pain searching on their phone, "
        "and ADA accessibility for mobility-impaired patients who depend on your booking form.",
        "Patients booking chiropractic care often search during acute pain — they need to find "
        "your number and tap to call immediately. {hl0} is a conversion finding that affects "
        "that critical mobile moment. The report covers {finding_count} issues including "
        "click-to-call optimization, above-fold booking CTA, appointment form security for "
        "health insurance data, and the local SEO gaps preventing your practice from appearing "
        "above competing clinics in the local map pack.",
        "ADA accessibility is a real issue for chiropractic practices — many of your patients "
        "have mobility limitations or chronic pain conditions that affect how they interact "
        "with web forms and booking pages. {hl0} is an accessibility finding with a direct "
        "patient care implication. The report covers {finding_count} issues including WCAG "
        "compliance for your booking form, intake form security for health insurance data, "
        "local SEO for 'chiropractor near me' searches, and review schema for Google star ratings.",
    ],
    "tech_startup_cto": [
        "For a B2B SaaS company in enterprise sales, {hl0} is a finding that enterprise "
        "security teams will surface in vendor risk assessments. The report covers "
        "{finding_count} issues including OWASP Top 10 compliance for your marketing site, "
        "security header configuration, GDPR/CCPA cookie consent and contact form data handling, "
        "and ADA compliance — all mapped to severity levels your security team can track "
        "in your existing vulnerability management process.",
        "Enterprise procurement teams run their own security scans before signing contracts. "
        "{hl0} is a finding that would appear in a standard vendor risk questionnaire and "
        "could delay or block a deal. The report covers {finding_count} issues with exact "
        "remediation steps your engineering team can implement: specific HTTP header values, "
        "DNS record configurations, and code-level fixes — no vague 'improve your security' "
        "recommendations. Developer-ready, with OWASP category mappings included.",
        "GDPR/CCPA compliance on your marketing site is a procurement blocker for EU and "
        "California enterprise customers who review vendor data practices. {hl0} is a "
        "compliance finding that creates audit exposure. The report covers {finding_count} "
        "issues including cookie consent implementation gaps, form data handling, email "
        "authentication to prevent phishing from your domain, and ADA accessibility — "
        "all with exact fix instructions your engineering team can ship in a sprint.",
    ],
    "spa_salon_owner": [
        "For a spa or salon whose clients book on mobile, {hl0} is a finding that directly "
        "affects your online booking conversion. The report covers {finding_count} issues "
        "including mobile page speed, local SEO for 'salon near me' and 'spa near me' searches, "
        "booking form accessibility, and Google review schema so your star ratings appear "
        "in local search results — all explained without technical jargon.",
        "{hl0} is one of {finding_count} findings that affect how easily new clients "
        "find and book your services online. The report includes specific fixes for your "
        "Google Maps local listing visibility, mobile gallery load speed, "
        "appointment booking form usability, and ADA accessibility for clients with disabilities — "
        "framed in terms of client bookings and new customer acquisition from Google searches.",
        "Slow mobile gallery pages and missing local SEO signals are the two biggest "
        "booking killers for spas and salons. {hl0} is a finding we identified in your "
        "site. The full report covers {finding_count} issues with step-by-step fixes "
        "your web manager can implement this week — from adding review star ratings "
        "in Google results to speeding up your service portfolio images.",
    ],
    "real_estate_agent_owner": [
        "For real estate, {hl0} is a finding that could be costing you qualified buyer and "
        "seller leads from Google organic search. The report covers {finding_count} issues "
        "including page load speed for your IDX property listings, local SEO for '[city] "
        "real estate agent' searches, email authentication to prevent wire fraud domain "
        "spoofing, and contact/home-valuation form conversion friction — all tied to leads.",
        "Real estate domain spoofing is one of the most common wire fraud attack vectors — "
        "clients lose earnest money because someone spoofed their agent's email. {hl0} "
        "is an email authentication finding that addresses that risk directly. The report "
        "covers {finding_count} issues including DMARC/SPF/DKIM configuration, IDX "
        "listing performance, mobile experience for buyers browsing listings, and lead "
        "capture form friction that causes buyer abandonment mid-search.",
        "{hl0} is among {finding_count} findings in your web presence assessment. For "
        "a real estate agent, slow listing pages, mobile UX gaps, and missing local "
        "SEO signals translate directly into lost leads before the first showing. "
        "The report gives you a prioritized action list with time estimates so you "
        "know exactly what to fix first to recover leads from Google organic traffic.",
    ],
    "franchise_expansion_buyer": [
        "For a multi-location franchise, {hl0} is an issue that likely appears across "
        "all your location websites — not just the main site we assessed. The report "
        "covers {finding_count} findings organized by fix priority, with a roadmap "
        "structure that makes it easy to hand off to your franchisor's IT team or "
        "deploy consistently across all locations. Local SEO consistency, ADA compliance, "
        "and email authentication are addressed with franchise-wide impact in mind.",
        "When you're running {finding_count} issue types across 3–5 locations, the biggest "
        "risk is inconsistency — different local SEO signals, different ADA exposure, "
        "different email authentication configurations per domain. {hl0} is an example "
        "of a finding that compounds across locations. This report gives you a repeatable "
        "audit baseline you can apply to each location site to identify which ones "
        "need the most immediate attention.",
        "{hl0} is among the {finding_count} findings in your web presence assessment. "
        "For franchise operators, the value isn't just fixing one site — it's building "
        "a systematic understanding of what to look for across your entire location portfolio. "
        "The prioritized roadmap format translates directly into a task list your webmaster "
        "can execute location by location, starting with the highest-impact items first.",
    ],
    "anxious_solopreneur": [
        "I know technical reports can feel overwhelming, so let me start with the most "
        "important finding for you right now: {hl0}. Of the {finding_count} items in "
        "this report, most have plain-English explanations and several are things you "
        "can fix yourself in your website platform today — no developer needed. "
        "The report clearly labels which fixes are 'no-code' versus 'needs a developer'.",
        "The good news: {hl0} is a finding that you can address yourself through your "
        "website settings — no technical knowledge required. The report covers "
        "{finding_count} issues total, but we've organized them into three tiers: "
        "things you can do today, things that take an afternoon, and things to put "
        "on a future to-do list. Most solopreneurs can resolve the top-priority items "
        "in an afternoon using the step-by-step guidance provided.",
        "For a one-person business like yours, not everything in this {finding_count}-issue "
        "report needs to be fixed immediately. {hl0} is the highest-priority item for "
        "your situation. The report is designed so you can hand it to your website "
        "platform's support chat (Wix/Squarespace/WordPress.com support) and ask them "
        "to walk you through the fixes — the report gives them all the detail they need.",
    ],
    "nonprofit_executive_director": [
        "The {finding_count}-finding report documents your compliance gaps with WCAG 2.1 AA "
        "standards, including {hl0}. Several major foundations now require WCAG compliance "
        "as a grant condition — having a documented audit baseline and remediation roadmap "
        "demonstrates proactive capacity-building that aligns with most grant reporting requirements. "
        "The report maps each finding to the specific WCAG success criterion, which is the "
        "language grant officers look for.",
        "On email authentication: {hl0} is exactly the type of domain vulnerability that "
        "scammers exploit to send fraudulent fundraising emails impersonating your organization. "
        "The report documents the current gap and provides a free step-by-step fix for SPF, "
        "DKIM, and DMARC records that your hosting provider or IT volunteer can implement "
        "in under an hour at no cost. This directly addresses donor trust.",
        "The report identifies {finding_count} issues and flags which ones qualify as 'free "
        "fixes' — things your hosting provider or a tech-savvy volunteer can address without "
        "paid developer time. Donor-facing security signals (HTTPS, privacy policy link, "
        "visible trust badges on the donation page) are addressed with specific, no-cost "
        "actions. We also flag which findings are most relevant to grant compliance language.",
    ],
    "tech_savvy_diy_owner": [
        "Based on the {finding_count}-finding report, {hl0} is a gap that most DIY "
        "site owners miss because it's not covered in standard SEO guides — it's in the "
        "server-level response headers, not the page content. The report goes beyond Yoast "
        "meta tags into areas like security header configuration, email authentication "
        "alignment between SPF/DKIM/DMARC, and WCAG 4.1.2 ARIA compliance — all of which "
        "affect trust, rankings, and liability exposure in ways that typical SEO blog posts "
        "don't address.",
        "The scan found {finding_count} issues — some you may already know about, and some "
        "that are genuinely difficult to detect without active header inspection and multi-page "
        "crawl analysis. {hl0} is a finding that appears invisible from the browser but is "
        "flagged by Google's security evaluation and email receiver scoring. Even well-maintained "
        "sites built by knowledgeable owners have these gaps because they require checking "
        "DNS records, HTTP response headers, and JavaScript execution context simultaneously.",
        "I'd challenge any SEO tool subscription to flag {hl0} at this level of specificity. "
        "The report includes the exact HTTP response data, the specific WCAG criterion violated, "
        "and the server directive needed to remediate — not a generic recommendation. "
        "For a technically capable owner, this is the difference between knowing there's a "
        "problem and knowing the exact two-line config change that fixes it.",
    ],
    "cybersecurity_msp_prospect": [
        "The {finding_count}-finding report covers the web application layer — security "
        "headers, WCAG ADA compliance, email authentication alignment, and SEO structure — "
        "none of which are visible to Qualys or Nessus endpoint scans. {hl0} is an example "
        "of a finding that only appears when you actively inspect HTTP response headers and "
        "DNS records simultaneously. For MSPs looking to expand their service portfolio, "
        "this audit layer is the gap between 'we protect your network' and 'we protect "
        "your entire digital presence from client-facing risk.'",
        "White-label packaging for MSP resale is straightforward: the report format is "
        "client-ready with your branding, and the {finding_count} findings are organized "
        "by business impact rather than CVSS severity — making them accessible to SMB "
        "owners who aren't reading vulnerability advisories. {hl0} is the kind of finding "
        "that creates an immediate conversation with a business owner about 'why does my "
        "email keep getting marked as spam' or 'my ADA lawsuit risk' — natural upsell "
        "conversations for an MSP's existing client relationship.",
        "For ConnectWise or Autotask ticket generation: the {finding_count} findings in "
        "this report export as structured data with category, severity, and remediation "
        "fields that map directly to service ticket templates. {hl0} would translate to a "
        "server configuration or DNS change ticket that your NOC team can action. "
        "This gives MSPs a repeatable web presence audit workflow they can run for "
        "each client quarterly with no manual effort beyond report delivery.",
    ],
    "interior_designer_owner": [
        "Gallery performance is the conversion bottleneck this report specifically addresses. "
        "{hl0} is among the {finding_count} findings, and the performance section identifies "
        "exactly which image loading pattern is causing your gallery pages to lose prospective "
        "clients on mobile before they see your best work. Interior photography is inherently "
        "high-resolution — the report provides the specific lazy loading, WebP conversion, "
        "and preload optimization steps that design portfolio sites need to eliminate that gap.",
        "For Google Images SEO: the {finding_count}-finding report includes schema markup "
        "findings that directly affect whether your portfolio photography appears in image "
        "search results for '[city] interior designer' queries. {hl0} is an example of "
        "the structured data gap that causes Google to index your images without attributing "
        "them to your studio — a missed opportunity when potential clients are searching "
        "specifically for the style you specialize in.",
        "Your website is the first design impression a high-value client gets before they "
        "contact you. {hl0} is a finding that a discerning client would notice directly — "
        "whether it's a slow gallery, a broken mobile layout, or a form that doesn't "
        "instill confidence when they're sharing renovation budget details. The "
        "{finding_count} findings in this report are prioritized by the ones most likely "
        "to affect a luxury client's decision to reach out or move on.",
    ],
}


def _format_fallback(template: str, report_highlights: list[str]) -> str:
    hl = list(report_highlights) + ["a critical finding", "an SEO gap", "an accessibility issue"]
    return template.format(
        hl0=hl[0],
        hl1=hl[1] if len(hl) > 1 else "an SEO gap",
        hl2=hl[2] if len(hl) > 2 else "an accessibility issue",
        finding_count=str(len(report_highlights)) if report_highlights else "multiple",
    )


def _agent_turn(
    prior: list[dict[str, str]],
    *,
    scenario: str,
    settings: AgentSettings,
    use_llm: bool,
    report_highlights: list[str] | None = None,
) -> str:
    if not use_llm:
        fallback_templates = _SCENARIO_FALLBACKS.get(scenario, [])
        turn_index = max(0, len([t for t in prior if t.get("role") == "agent"]) - 1)
        if fallback_templates:
            template = fallback_templates[turn_index % len(fallback_templates)]
            return _format_fallback(template, report_highlights or [])
        return (
            "Fair point. The report includes page-level evidence, screenshots, and a prioritized roadmap "
            "so your team can execute fixes with clear business impact."
        )
    client = OllamaClient(settings)
    result = client.chat_json(
        system=(
            "You are Neil Fox selling a premium web presence report over email only. "
            "Be concise, specific, trustworthy, and handle objections with evidence/process. "
            "Return JSON with key 'reply'."
        ),
        user=str({"scenario": scenario, "prior": prior[-6:], "highlights": (report_highlights or [])[:3]}),
        schema_hint={"type": "object", "properties": {"reply": {"type": "string"}}},
    )
    try:
        llm_reply = validate_sales_reply_payload(result)
    except ValueError:
        llm_reply = ""

    def _looks_weak(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return True
        weak_markers = [
            "totally fair question",
            "evidence-backed",
            "without guesswork",
            "[name]",
            "thanks for reaching out",
            "book a call",
            "schedule a",
            "sync",
            "zoom",
            "quick call",
            "10-min",
        ]
        if any(m in t for m in weak_markers):
            return True
        return len(t.split()) < 20

    if llm_reply and not _looks_weak(llm_reply):
        return llm_reply

    fallback_templates = _SCENARIO_FALLBACKS.get(scenario, [])
    if fallback_templates:
        turn_index = max(0, len([t for t in prior if t.get("role") == "agent"]) - 1)
        template = fallback_templates[turn_index % len(fallback_templates)]
        return _format_fallback(template, report_highlights or [])
    return (
        "I included page-level evidence, screenshots, and a clear 30/60/90-day action plan so your team can implement fixes quickly over email."
    )


def _user_turn_template(scenario_key: str, turn_no: int) -> str:
    templates = {
        "skeptical_owner": [
            "How do I know this isn't just generic fluff?",
            "What exact evidence do I get?",
            "Can you show me what was actually checked?",
        ],
        "price_sensitive": [
            "Why is this worth $299?",
            "Can I do this myself with free tools?",
            "What ROI should I expect?",
        ],
        "technical_operator": [
            "Which pages are failing and why?",
            "Do you include remediation steps or just findings?",
            "Can we prioritize fixes by impact?",
        ],
        "busy_decider": [
            "I have five minutes. Give me the short version.",
            "What are the top 3 urgent issues?",
            "What do I do first tomorrow?",
        ],
        "curveball_scope": [
            "Can you include competitor comparisons too?",
            "Can you add copy rewrites for the homepage?",
            "Can this include ad landing page suggestions?",
        ],
        "compliance_cautious": [
            "How reliable are ADA findings?",
            "Are you giving legal advice?",
            "How should we validate before implementing?",
        ],
        "refund_risk": [
            "What if I don't find this useful?",
            "How do you avoid false alarms?",
            "Can I request revisions?",
        ],
        "timeline_pressure": [
            "Can I act on this today?",
            "How soon can the first fixes go live?",
            "What should my developer tackle first?",
        ],
        "comparison_shopper": [
            "I've gotten proposals from two other agencies. How is this different?",
            "The others said they'd do a full audit too. What makes yours better?",
            "Can you show me a sample output so I can compare?",
        ],
        "repeat_skeptic": [
            "OK but how do I know the findings are actually accurate?",
            "That sounds reasonable, but what if my developer says these aren't real issues?",
            "I'm still not convinced this is worth $299. Give me one more reason.",
        ],
        "already_has_agency": [
            "We already work with a web agency. Why would we need this?",
            "Our agency handles SEO and security — aren't they covering this?",
            "What would this report show that our agency hasn't already told us?",
        ],
        "data_privacy_concerned": [
            "What data did you collect from our website to run this audit?",
            "Did you store any visitor data or access anything private on our server?",
            "Can you confirm this was a passive scan and you didn't touch our backend?",
        ],
        "overwhelmed_owner": [
            "I know there are issues but I don't even know where to start. Is this going to add to my to-do list?",
            "My developer is already swamped. Which of these actually needs to be fixed first?",
            "There are so many findings. Can you just tell me the three things that matter most?",
        ],
        "seo_focused_buyer": [
            "I mainly care about ranking on Google. Do I really need all the security and ADA stuff too?",
            "How does fixing security headers actually affect my search rankings?",
            "What's the direct SEO lift I'd see from fixing the top findings in this report?",
        ],
        "mobile_first_buyer": [
            "Most of my customers use their phones. Are these findings affecting my mobile visitors specifically?",
            "How does the mobile load time compare to desktop in this report?",
            "What should my developer fix first that's specifically a mobile problem?",
        ],
        "accessibility_attorney": [
            "We got an ADA demand letter last week. Do the findings in this report relate to WCAG 2.1 AA violations?",
            "My lawyer needs specific WCAG criteria violations with element-level evidence. Does the report include that?",
            "Are these findings documented well enough to show good-faith remediation effort to the plaintiff?",
        ],
        "performance_anxious": [
            "I just ran PageSpeed Insights and got a 38 on mobile. What's causing it and what do I fix first?",
            "My developer says the site is 'fast enough' but Google is saying otherwise. Who's right?",
            "Will fixing these performance issues actually move my PageSpeed score or are there other factors?",
        ],
        "roi_focused_buyer": [
            "What's the exact ROI on a $299 investment here? Give me numbers, not vague estimates.",
            "How did you arrive at the revenue projections in the report? What assumptions are you making?",
            "What's the payback period if I fix just the top 5 issues?",
        ],
        "quick_start_buyer": [
            "OK I'm in. What are literally the first 2–3 things I should do this week?",
            "Which of these can my developer ship today without breaking anything?",
            "What's the one change that has the most immediate impact on leads or rankings?",
        ],
        "cybersecurity_worried": [
            "Our domain was used to send phishing emails to our own customer list last month. How does this report help prevent that from happening again?",
            "Our IT company said our site is 'fine' but we keep seeing suspicious login attempts in our hosting logs. What specific security problems did your scan actually find?",
            "Can you walk me through exactly which findings in the report stop someone from spoofing our email domain? That's the main thing I need to fix right now.",
        ],
        "franchise_owner": [
            "I'm part of a franchise system and need corporate approval before making any site changes. Will this report give me enough documentation to justify the work to my corporate team?",
            "My franchise agreement requires a certain level of website security and ADA compliance. Does your report map to those standards in a format I can present to corporate?",
            "How do I know these findings are real problems and not just your tool flagging things that don't matter? My corporate IT team will push back hard on anything vague.",
        ],
        "healthcare_compliance_buyer": [
            "We handle patient inquiries through our website contact form. Does this report flag any HIPAA or PHI exposure risks in how that form is set up?",
            "We had a compliance audit last year and ADA was flagged as a risk area. Does your report give us enough WCAG-level detail to show our attorney we're remediating in good faith?",
            "My practice manager wants to know: did your scan access any patient data, appointment records, or anything behind our login? We need a clean data handling statement.",
        ],
        "ecommerce_cro_owner": [
            "Our cart abandonment rate jumped from 68% to 79% in the last quarter. Do the findings in this report connect to what's driving that?",
            "We've been told our checkout page is slow but our dev says it's fine. What does your scan actually show for load time on checkout specifically?",
            "Our biggest issue is that customers add to cart but don't complete purchase. Which findings in this report are most directly connected to purchase completion rates?",
        ],
        "social_proof_seeker": [
            "How do I know your methodology is reliable? Do you have case studies showing these recommendations actually improved someone's site?",
            "What makes this different from just running a free tool like GTmetrix or Google's PageSpeed Insights myself?",
            "Can you walk me through exactly how you verified the top finding? I want to understand the evidence chain before I pay.",
        ],
        "enterprise_it_manager": [
            "Does this report map findings to OWASP Top 10 or CWE IDs? Our internal tracking system requires a standard classification.",
            "How are severity ratings assigned? Are these CVSS scores or proprietary? I need to justify the priority order to my security team.",
            "We already run quarterly pen tests. What does this report surface that a professional pen tester wouldn't find?",
        ],
        "budget_constrained_nonprofit": [
            "We're a nonprofit with basically no IT budget. Are any of these fixes actually free to implement?",
            "Our board keeps saying ADA compliance is a legal risk but we don't know where to start. Does this report give us enough to show we're taking it seriously?",
            "Which of the fixes in this report can our office manager handle without needing to hire a developer?",
        ],
        "multi_location_owner": [
            "We have four locations and each has its own page on the site. Does this report tell me which location has the worst issues?",
            "If I fix the issues on the main site, will that help all the location pages automatically or do I need to fix each one separately?",
            "My biggest concern is local Google rankings — we keep losing to competitors in the map pack for certain locations. Do the findings here explain why?",
        ],
        "local_seo_buyer": [
            "We rely almost entirely on Google Maps and 'near me' searches. Does this report actually help with local pack rankings or is it mostly generic technical stuff?",
            "My competitors are showing up in the map pack above us even though we've been here longer. Can you tell from the scan why that might be happening?",
            "Do the findings cover Google Business Profile or NAP consistency issues? That's the main thing I've been told affects local ranking.",
        ],
        "gdpr_anxious_buyer": [
            "Before I even look at the report — did your scan collect any visitor data from my website or access anything that could be considered personal data?",
            "We have customers in Germany and Canada. The report mentions cookie consent issues — how serious is the exposure and what's the actual fix?",
            "Our lawyer mentioned CCPA and GDPR in the same breath last week. Does this report help us show compliance or does it just point out more problems?",
        ],
        "restaurant_owner": [
            "We're a local restaurant and 80% of our new customers find us on Google Maps. Does this report actually help with local pack ranking or is it just generic website stuff?",
            "Our online ordering is through a third-party platform but our menu is on our own site. Are there findings that affect how our menu ranks in Google search?",
            "We're losing reservations to a newer restaurant that opened nearby. Can you tell from the scan why they might be ranking above us in local results?",
        ],
        "legal_professional": [
            "We're a law firm. Our state bar has specific rules about attorney advertising online. Does your report flag anything that could be an ethics issue under those rules?",
            "Our contact form collects client name and legal matter type. Is there a security finding in this report that covers how that data is transmitted?",
            "We received an ADA demand letter six months ago and settled. We need to make sure we're not exposed again — does this report give us enough WCAG detail to show good-faith compliance?",
        ],
        "referral_partner": [
            "My accountant/attorney sent me your way. I've been burned by digital agencies before — what proof do you have that this report is different from generic tool output?",
            "Can you share references from similar businesses in my industry who found this useful? I want to talk to someone who actually acted on this report.",
            "Walk me through exactly how the ROI math works for my specific site. What's the realistic payback period if I implement the top 5 findings?",
        ],
        "review_reputation_buyer": [
            "Our Google rating dropped from 4.8 to 4.2 after one bad review went viral. Does this report address why our star rating isn't showing in search results?",
            "Our competitor has 340 reviews and a 4.9 star rating showing right in Google search. We have 89 reviews and nothing shows. What's causing that difference technically?",
            "I've heard about review schema and Google Business Profile optimization. Does this report actually cover those things specifically or is it mostly generic website stuff?",
        ],
        "b2b_saas_founder": [
            "We're in the middle of our first enterprise deal and the customer's security team asked for a vendor risk assessment. Does this report cover the technical findings they'd care about?",
            "Our free-trial signup page has a 2.3% conversion rate and I suspect trust signals are the issue. Does the report identify specific missing trust elements on landing pages?",
            "We're working toward SOC 2 Type II and our auditor mentioned that having documented security assessments on our web properties helps. Would this report satisfy that requirement?",
        ],
        "home_services_owner": [
            "I run a plumbing company. I'm not tech-savvy at all. Can someone like me actually understand and use this report, or is it all developer jargon?",
            "My competitor shows up first in Google Maps and I don't even though I've been in business longer. Is that a website issue this report would address?",
            "I get most of my jobs from people calling me from their phone after a Google search. How would fixing what's in this report translate into more phone calls?",
        ],
        "dental_practice_owner": [
            "We have an online appointment form — does the report check whether that form is HIPAA-safe or could expose patient information?",
            "We've had elderly patients tell us the website is hard to use. Would the accessibility findings cover things like font size, button size, and screen reader compatibility?",
            "Our front office manager handles the website. Can the report separate what she can fix herself in our website builder from what needs our IT vendor?",
        ],
        "fitness_studio_owner": [
            "Most of our students find us on Instagram and then check our website on their phone. How would this report help us get more people to actually book a class?",
            "There's a bigger studio that opened two blocks away and they started showing up above us in Google Maps. Would this report tell us what they might be doing differently?",
            "Our class schedule and booking button aren't on the homepage — could that be hurting us and is that the kind of thing the report covers?",
        ],
        "print_media_traditionalist": [
            "I've advertised in the Yellow Pages for 30 years. I honestly don't believe my customers search for me on Google — they call from the phonebook or come in because of referrals. Why would I pay $299 for a website report?",
            "My nephew built my website two years ago. It looks fine to me. What would an 'audit' actually find that he didn't already fix?",
            "I just want more phone calls and walk-ins. How does fixing website stuff translate into actual customers? Give me one example that makes sense.",
        ],
        "first_time_website_owner": [
            "I literally just launched my first website last month. I don't even know what half of these terms mean — what's DMARC? What's a meta description? Why would my brand-new site already have problems?",
            "My web designer said my site was done. Now you're saying there are issues. Should I go back to them? Is this something they missed, or is this stuff they weren't supposed to do?",
            "This is overwhelming. Can you just tell me the top two things I should worry about as someone who just started? I don't want to spend months fixing technical stuff.",
        ],
        "budget_approval_needed": [
            "We have internal approval thresholds and anything over $500 needs sign-off from our CFO. Can you give me a one-page executive summary I could present to justify the spend?",
            "Our CFO is going to ask about ROI and payback period. Do you have specific numbers or benchmarks that show what kind of return businesses typically see after fixing these kinds of issues?",
            "What's the risk if we don't act on this now? I need to frame the downside for leadership — what does the competitive situation look like if a rival fixes these issues first?",
        ],
        "already_has_seo_agency": [
            "We already pay $1,500 a month to an SEO agency. What would this report tell us that they're not already covering? I don't want to pay for duplicate work.",
            "Our agency does monthly audits. I'm looking at their last report right now — it covers keyword rankings, backlinks, and on-page optimisation. How is what you found different from that?",
            "If we shared this report with our SEO agency, would it conflict with their strategy, or is it the kind of thing they'd incorporate into their existing work?",
        ],
        "insurance_agent_owner": [
            "We handle a lot of sensitive client data — quote requests, existing policy numbers, renewal inquiries. What did the scan find that could be a data exposure risk for our clients?",
            "Our E&O carrier specifically asks about cybersecurity practices at renewal. If there are security findings in this report, does fixing them actually reduce our liability, and how would I document that?",
            "We've had clients tell us they got a suspicious email pretending to be from our agency. Does this report cover how to stop someone from spoofing our email address?",
        ],
        "childcare_provider_owner": [
            "Most parents find us on their phones. How do the findings in this report affect what they see when they search 'daycare near me' on a smartphone?",
            "We ask parents to fill out an enrollment inquiry form on the website. Is that form secure? I'd be mortified if someone's information got exposed.",
            "A competitor down the street seems to show up higher on Google Maps than us. Did your scan find anything specific about why that might be happening and what we could do about it?",
        ],
        "physical_therapist_owner": [
            "We have elderly and post-surgery patients who use our website to book appointments. What specific ADA findings in the report affect those patients most directly?",
            "Our online intake form asks for health insurance information and injury history. Did the scan flag anything about how that data is being handled or transmitted?",
            "We're trying to reduce how many new patients we get through physician referrals and increase direct web bookings. Which findings in the report most directly affect that goal?",
        ],
        "auto_repair_shop_owner": [
            "I'm not technical at all. Can you explain what the top finding actually means in plain English — like what's actually broken and how it affects my business?",
            "My competitor across the street has way more Google reviews than me but mine are all 5 stars. Why are they still showing up above me in Google Maps and does this report explain that?",
            "Most of my calls come from people searching on their phone while their car is broken down. Are there findings that specifically affect how my shop shows up for those mobile searches?",
        ],
        "accountant_practice_owner": [
            "We have business clients who send us sensitive financial documents through the contact form on our website. Did the security scan find anything that could expose those documents or the client information submitted?",
            "I've had two clients tell me they received suspicious emails that looked like they came from our firm's email address. Does the email authentication section of this report explain how that's happening and what stops it?",
            "Our state CPA licensing board has requirements around client data handling. If there are security findings, can I document the remediation steps as evidence of due diligence for our E&O insurance renewal?",
        ],
        "veterinary_clinic_owner": [
            "Most of our emergency calls come from people who found us on Google Maps at odd hours. What findings in the report specifically affect how we appear in 'emergency vet near me' or 'vet near me' searches?",
            "We've been trying to get our Google star rating to show up in search results alongside our listing. Did the scan find any reason our review stars aren't appearing and what would fix that?",
            "A lot of panicked pet owners are on their phone searching while their pet is in distress. Are there mobile experience findings that would affect whether someone can find our number and tap to call us quickly?",
        ],
        "property_management_owner": [
            "We have a tenant application form on our site that asks for SSN and income documentation. Did the security scan flag anything about how that sensitive data is being handled or transmitted?",
            "We're trying to rank higher than competing property managers in the local area for search terms like 'property management [city]'. What did your scan find that's affecting our local search visibility specifically?",
            "Are there any ADA or Fair Housing Act compliance issues in the report? We manage residential properties and I know accessibility obligations extend to digital listings under federal law.",
        ],
        "nonprofit_board_member": [
            "Our largest funder recently added a WCAG 2.1 AA accessibility requirement to their grant terms. What ADA findings in the report would be most relevant to document for our grant compliance file?",
            "We've had donors call us worried they got a suspicious fundraising email asking for a donation that looked like it came from us. Does the email security section explain how to stop someone from impersonating our organization?",
            "Most of our remediation budget is volunteer time or donated services — we genuinely cannot afford a developer. Which fixes in the report are zero-cost or can be done through a plugin without coding?",
        ],
        "tutoring_center_owner": [
            "Our enrollment inquiry form asks for the child's name, grade level, and parent contact info. Did the security scan find anything that could expose that information — we work with minors and that makes me nervous.",
            "We're trying to show up when parents search for 'SAT prep near me' or 'tutoring center [city]' on their phones after school. Which findings in the report most directly affect our visibility in those local searches?",
            "We have a competitor who started a few months ago and they're already showing up above us in Google Maps even though we've been operating for 6 years. Is there something specific in this report that explains why and what we can do about it?",
        ],
        "boutique_hotel_owner": [
            "We pay 18% commission on every Booking.com reservation. I'm told our direct booking rate is only 22%. If we fix the website issues in this report, what's a realistic improvement in direct booking conversion I should expect?",
            "Our gallery page has about 60 high-resolution room photos. I know it loads slowly — would fixing the image loading issues in the report actually make a measurable difference in how long guests stay on the page and whether they book directly?",
            "We're not showing up in Google's hotel results with star ratings even though we have 140 reviews. Our competitor hotel down the street shows 4.7 stars right in Google search. What's causing that and is it in this report?",
        ],
        "photography_studio_owner": [
            "My portfolio page has about 80 full-resolution wedding photos — it loads really slowly on phones but I don't know if it's the images, the WordPress theme, or something else. Does the report tell me specifically what's causing the slowness and how to fix it without losing photo quality?",
            "I've been getting more Instagram followers but my website contact form inquiries are flat. A friend mentioned something about my Google rankings — does the SEO section of the report explain why I'm not showing up for searches like 'wedding photographer [my city]'?",
            "I had a prospective client email me asking if my contact form is secure because they were nervous about submitting their event date and personal info online. What does the security section of the report say about my contact form and what would it take to fix any issues found?",
        ],
        "financial_advisor_owner": [
            "A client forwarded me a suspicious email that appeared to come from our domain asking for investment account credentials. We're almost certain it was a spoof — does the email authentication section of the report tell us definitively whether someone can send emails impersonating our firm, and what specifically would stop it?",
            "We have retired clients in their 70s who use screen readers and accessibility tools. Our compliance officer mentioned something about ADA Title III applying to financial services websites. How serious are the accessibility findings in the report and what would our exposure be if a client filed a complaint?",
            "Our online contact form asks for name, phone, and a description of financial goals. FINRA has strict rules about what we can say online to prospective clients — does the security section of the report cover whether that form data is encrypted in transit, and is there anything in the findings that could create a compliance documentation issue for our next audit?",
        ],
        "optometry_practice_owner": [
            "A lot of my patients are in their 50s, 60s, and 70s — many with early presbyopia or macular degeneration. The irony isn't lost on me that an eye doctor's website might be hard for vision-impaired people to use. Does the accessibility section of the report specifically cover color contrast, text sizing, and whether our booking form is usable with screen readers?",
            "We collect vision insurance information on our online appointment request form — insurance plan, group number, date of birth. Is that considered sensitive enough health data that the security findings in the report apply to our form security? And what would it actually take to fix the issues found?",
            "I searched 'optometrist near me' and one of my competitors shows 4.9 stars right in Google search with a little star rating bar. We have more reviews but nothing shows up for us. Is that a schema issue that's in the report and how complicated is it to fix?",
        ],
        "landscaping_business_owner": [
            "I keep seeing ABC Landscaping come up above me on Google Maps and I know for a fact I have more reviews than they do. I've asked three different people why this happens and nobody gives me a straight answer. Does your report actually explain specifically what they're doing right that I'm not, or is this just generic advice I've already heard?",
            "Most of my customers are searching from their phones. I tested my own website on my phone last week and it took forever to load — like 8 seconds or something. Does the report break down exactly which things are causing that slowdown, and are they things I can fix myself or do I need to hire someone?",
            "I'm in peak season April through October and bone dry in December and January. If I fix the website stuff in this report, is there a realistic chance it moves the needle for me before this spring, or is this something where I won't see any results for 6 months?",
        ],
        "wedding_venue_owner": [
            "We get a lot of brides who find us on The Knot and WeddingWire but our own website doesn't seem to show up when people search 'outdoor wedding venue [our city]'. Does your report cover why we're not showing in Google and what specifically we'd need to fix to start appearing for those searches?",
            "I tested our gallery page on my phone and the photos take forever to load. It takes like 12 seconds before you can even see the first full image. Is that the kind of performance problem your report identifies, and does it tell us how to fix it without losing image quality?",
            "We had a competing venue open 18 months ago with half our capacity and they already outrank us on Google Maps. They have 40 reviews and we have 90. What does your report say about why newer venues can outrank established ones and what we can realistically do before January inquiry season?",
        ],
        "e_learning_platform_owner": [
            "We have students in Germany, the UK, and California — I know GDPR applies but I'm genuinely unclear whether our enrollment forms and student data handling are compliant. Does your report include specific findings about whether our forms and cookie implementation meet those requirements?",
            "Several students have emailed us saying they have difficulty using our course enrollment page with a screen reader. We've been meaning to fix this for a year but don't know where to start. Does the accessibility section of your report identify the specific WCAG violations that would explain what screen reader users are experiencing?",
            "We rank well for our brand name but we don't show up for '[our course topic] online course' searches where people don't know our name yet. Does the report cover the technical SEO gaps that explain why we're invisible for those high-intent discovery searches?",
        ],
        "chiropractor_practice_owner": [
            "I searched 'chiropractor near me' yesterday and three competitors showed up in the Google map pack above me even though I've been practicing here for 12 years. Does your report tell me specifically what they're doing that I'm not, or is this just generic SEO advice?",
            "My patients fill out a health intake form on my website that asks about their injuries, insurance, and medical history. Is that data secure? I'd be horrified if a patient's health information got exposed through my website.",
            "A lot of my patients call when they're in pain and in a hurry. My phone number is on the website but a patient told me last week they couldn't figure out how to tap it to call from their phone. Does the report cover that kind of mobile usability issue and how to fix it?",
        ],
        "tech_startup_cto": [
            "We're in the middle of an enterprise deal and the customer's security team sent us a vendor risk questionnaire that includes questions about our public web properties. Does this report give us findings that map to standard security questionnaire categories like OWASP or NIST?",
            "We process data from EU and California customers through our marketing site contact forms. Our legal team flagged GDPR and CCPA as risk areas but we haven't done a proper audit. Does the report cover whether our cookie consent and form data handling actually meet those requirements?",
            "I'm a CTO so I want the technical details, not a sales pitch. Can you walk me through what specific evidence was collected for the top 2–3 security findings — like what HTTP response headers you saw, what DNS records you queried, and what page-level HTML you analyzed?",
        ],
        "spa_salon_owner": [
            "I use Vagaro for my online booking but most of my clients say they find me by searching 'facial near me' or 'spa in [city]' on Google. Why am I showing up on page 2 when a newer salon with half my reviews shows up first in the map pack?",
            "I had a client last week who said my website was hard to navigate on her iPhone — she almost didn't book because the gallery was loading too slowly. Does your report tell me exactly which images or pages are the problem and how much it's actually costing me?",
            "I have a couple of clients with visual impairments who use screen readers. I've been meaning to make the site more accessible but I don't know where to start or what I'm legally required to do. Does the report cover that in plain English without a lot of jargon?",
        ],
        "real_estate_agent_owner": [
            "I pay for Zillow Premier Agent and Realtor.com leads but the conversion rate is terrible compared to the clients who find me directly through Google. I know my website needs work but I don't know what specifically is holding me back in search rankings for '[city] homes for sale'.",
            "A colleague of mine had a client lose $50,000 in earnest money because someone spoofed my colleague's email domain and sent fraudulent wire instructions. I'm paranoid this could happen to my clients. Does the report tell me how to prevent that?",
            "I have a home valuation request form on my site but I think a lot of people start filling it out and then abandon it. Can the report tell me what's causing the abandonment and what changes would get more people to submit their contact info?",
        ],
        "franchise_expansion_buyer": [
            "We have 4 locations and our franchisor's IT team does a basic annual review but they focus entirely on our point-of-sale systems, not the websites. Our corporate brand team approved our sites but I don't think anyone has ever checked whether the local SEO signals are consistent across all locations or whether one location's email domain could affect the others.",
            "I'm specifically worried about ADA compliance because if one of our location websites gets sued, our franchisor's legal team made clear that the franchisee is responsible — not corporate. Does the report give us findings we can use to demonstrate we're proactively addressing accessibility, or is it just a list of problems without the documentation a lawyer would need?",
            "If this audit found issues on our main site that are likely the same across all our location sites, what's the most efficient way to approach fixing them — do we fix the template and push out to all locations, or does each location need its own remediation process?",
        ],
        "anxious_solopreneur": [
            "I built my website myself on Squarespace and honestly I'm terrified to touch anything now because the last time I tried to change a setting I broke the layout for 3 days. Can you tell me which of the findings in the report are things I could fix through the Squarespace dashboard without touching any code or risking breaking anything?",
            "I've gotten emails before from 'SEO experts' telling me my website has 47 critical errors and I need to pay $2,000 a month to fix them. I feel like this report is going to be the same thing. How do I know which of your findings actually matter for a one-person coaching business versus which ones only matter for large companies?",
            "I don't have a developer and can't afford to hire one right now. If I can only do three things from this report using my Squarespace platform's built-in settings or free apps, what are those three things and what would they actually change about how people find me or whether they contact me?",
        ],
        "nonprofit_executive_director": [
            "We had an incident last year where scammers sent emails to our donor list pretending to be from our organization asking for gift card donations. We sent an emergency retraction email but we still lost two major donors who were embarrassed. Does the report explain specifically what email authentication settings we're missing that allowed that to happen, and what it costs to fix?",
            "We're applying for a capacity-building grant from a foundation that requires WCAG 2.1 AA compliance as a condition. We have zero IT staff and a volunteer web team. Does the report give us enough documented evidence of our accessibility gaps and remediation plan that we could attach it to a grant application to show we're proactively addressing this?",
            "Our board chair asked me to justify any consultant spend over $500 at the next board meeting. Can you give me a one-paragraph statement I can read to the board that explains what this report covers, why it matters to our mission and donor trust, and what the alternative cost would be if we were served an ADA demand letter?",
        ],
        "tech_savvy_diy_owner": [
            "I already have Yoast SEO Premium installed and I run ahrefs monthly. I know about meta descriptions, sitemaps, and I've already fixed my H1 tags. What's in this report that's going to show me something I haven't already looked at myself?",
            "Can you walk me through specifically what the scan did to detect the security header issues? Like, did it actually make HTTP requests to check response headers, or is it just checking the HTML source? Because I've seen tools that claim to check security but they're just looking at meta tags, which tells you nothing about actual server configuration.",
            "I've been doing my own SEO for 3 years and I'm skeptical that a $299 report is going to tell me something I can't find myself with free tools. Give me your strongest argument for why this report is worth it for someone who already knows what they're doing — what does it surface that I genuinely couldn't find on my own?",
        ],
        "cybersecurity_msp_prospect": [
            "We currently run quarterly vulnerability scans with Nessus for all our clients' servers and endpoints. How is this web presence audit different from what we're already doing, and would our existing clients see value in adding it on top of their current managed security service?",
            "We have about 35 SMB clients ranging from law firms to medical practices to retail shops. What does the pricing structure look like if we're running this audit for multiple clients, and can the report be white-labeled with our MSP's branding instead of yours?",
            "Our clients keep asking why their emails go to spam and whether their websites are compliant with ADA — can your audit help us answer those specific questions with documented evidence we can actually show to a client who isn't technical?",
        ],
        "interior_designer_owner": [
            "I just had a potential client tell me they found my portfolio beautiful but my website was 'a little slow' and they ended up hiring someone else they found faster. How does your report identify which specific pages and images are causing that load time problem on mobile?",
            "I get a lot of referral traffic from Instagram and Pinterest but I can't tell if those visitors are actually filling out my consultation inquiry form or just leaving. Does the report analyze why that traffic isn't converting and what changes would help?",
            "I'm concerned about the contact form on my site — clients share renovation budgets, property addresses, and personal information through it. How does the report evaluate whether that form is actually secure, and what would an insecure form mean for my clients?",
        ],
    }
    seq = templates.get(scenario_key) or ["Tell me more."]
    if turn_no <= len(seq):
        return seq[turn_no - 1]
    overflow = {
        "skeptical_owner": "If I forward this to my developer, what's the first fix to ship this week?",
        "price_sensitive": "What's the fastest win I should expect in the first 30 days?",
        "technical_operator": "Can you rank the top 3 fixes by effort vs impact?",
        "busy_decider": "Give me the first two tasks I should assign today.",
        "curveball_scope": "If we keep scope fixed, what should we prioritize first?",
        "compliance_cautious": "What should legal review before we implement?",
        "refund_risk": "If one item is wrong, how do we handle revisions?",
        "timeline_pressure": "What can realistically go live this week?",
        "comparison_shopper": "What's your strongest differentiator vs standard agency audits?",
        "repeat_skeptic": "Give me one concrete proof point tied to my site.",
        "already_has_agency": "How should I hand this to my agency so they can execute quickly?",
        "data_privacy_concerned": "Can you confirm this remained read-only from end to end?",
        "overwhelmed_owner": "If I can only do one thing this month, what is it?",
        "seo_focused_buyer": "Which single fix would have the biggest positive impact on my rankings?",
        "mobile_first_buyer": "Which finding has the biggest impact on mobile conversions specifically?",
        "accessibility_attorney": "What's the priority order for remediation to minimize our legal exposure this month?",
        "performance_anxious": "After we fix the top performance issues, what should we measure to confirm the score improved?",
        "roi_focused_buyer": "Can you walk me through the conservative vs base vs upside scenario numbers one more time?",
        "quick_start_buyer": "After those first two fixes ship, what's the next priority the following week?",
        "cybersecurity_worried": "Can you prioritize just the email spoofing prevention steps for my IT team to start on this afternoon?",
        "franchise_owner": "What format do other franchise owners typically use when presenting this report to their corporate compliance team?",
        "healthcare_compliance_buyer": "Can you confirm in writing that the scan was passive — no PHI accessed, no patient data stored — so I can share that statement with our HIPAA officer?",
        "ecommerce_cro_owner": "After we fix the top checkout friction items, what metric should we watch first to confirm the cart abandonment rate is actually improving?",
        "social_proof_seeker": "Can you point me to the specific section of the report that has the most verifiable, independently confirmable evidence?",
        "enterprise_it_manager": "What's the process for getting the raw findings data in a machine-readable format for ingestion into our SIEM or ticketing system?",
        "budget_constrained_nonprofit": "Can you list just the zero-cost fixes from the report so I can take those to our next board meeting?",
        "multi_location_owner": "After we fix the site-wide issues, what's the recommended order for tackling location-specific improvements?",
        "local_seo_buyer": "Which single finding in the report has the most direct impact on our Google Maps ranking if we fix it this week?",
        "gdpr_anxious_buyer": "Can you confirm in a brief written statement that the scan was read-only and no visitor or customer data was collected or stored?",
        "restaurant_owner": "Which finding is most directly costing us reservations right now and how fast can it be fixed?",
        "legal_professional": "Can you provide a brief summary statement for our ethics review file confirming the scan methodology and the nature of the findings?",
        "referral_partner": "My advisor wants to review the deliverable before I commit. Can you send a sample report section showing the evidence format for the top finding?",
        "review_reputation_buyer": "If I implement the review schema fix this week, how long before I'd expect to see the star ratings appear in Google Search results?",
        "b2b_saas_founder": "Can you help me understand which findings in this report would be most relevant to include in our vendor security disclosure document for enterprise procurement?",
        "home_services_owner": "If I only had one hour this week to work on this, what's the single most impactful thing to fix to get more calls from Google searches?",
        "dental_practice_owner": "Which finding in the report would be the highest priority for our HIPAA officer and front-desk team to review together?",
        "fitness_studio_owner": "After we fix the top mobile and booking CTA issues, what's the best way to measure whether we're actually getting more class registrations from the website?",
        "print_media_traditionalist": "Give me one concrete example — not technical jargon — of how fixing something in this report would result in my phone ringing more often.",
        "first_time_website_owner": "If I hand this report to my web designer, what's the one sentence I should say to them so they know where to start?",
        "budget_approval_needed": "Can you send me a brief written summary of the top 3 findings and the ROI case that I can paste into our internal approval request?",
        "already_has_seo_agency": "Would you be willing to get on a call with our SEO agency so you can both explain how this report fits alongside their ongoing work?",
        "insurance_agent_owner": "Can you summarize in plain language what an E&O attorney would say is the highest-priority fix from this report to reduce our liability exposure?",
        "childcare_provider_owner": "If a parent searches Google for childcare in our zip code right now, which single fix from this report would have the most immediate impact on them finding us?",
        "physical_therapist_owner": "Which finding in the report should our patient care coordinator and web developer review together first to protect our elderly patients?",
        "auto_repair_shop_owner": "If I could only make one change this week to get more calls from Google Maps, which finding in the report gives me the fastest win?",
        "accountant_practice_owner": "Can you give me a one-paragraph summary of the email authentication findings I can paste into an email to my IT person so they know exactly what to do?",
        "veterinary_clinic_owner": "If we fix the top mobile and local SEO items, how long before we'd expect to see an improvement in the number of calls coming from 'vet near me' searches?",
        "property_management_owner": "If a prospective tenant visits our site right now from their phone, which single fix from this report would have the most immediate impact on whether they submit an application?",
        "nonprofit_board_member": "Can you provide a brief written summary of the ADA findings for our grant compliance file showing we identified and are addressing the specific WCAG issues?",
        "tutoring_center_owner": "If a parent searched for tutoring in our area on their phone right now and found our site, which single fix from this report would have the biggest impact on whether they submit an enrollment inquiry?",
        "boutique_hotel_owner": "If we fix the top booking CTA and mobile speed issues this week, what should we watch in our direct booking analytics to confirm we're actually converting more guests away from OTAs?",
        "photography_studio_owner": "If I fix the top portfolio image performance issues and SEO gaps this month, what metric should I track to know whether I'm getting more booking inquiry form submissions?",
        "financial_advisor_owner": "Can you provide a brief written summary of the email authentication findings that I can share with our compliance officer and IT consultant so they understand the scope and can start the DMARC/SPF remediation?",
        "optometry_practice_owner": "Can you give me a one-page written summary of the ADA and security findings that I could share with our office manager and our billing person so they understand what needs to be addressed on the patient intake form?",
        "landscaping_business_owner": "If I can only fix two things before spring season, which two items from the report will have the most direct impact on getting more quote requests from people who find us on Google?",
        "wedding_venue_owner": "If we implement the gallery performance fix and the review schema change before January, which metric should I watch in Google Analytics to know whether more brides are actually completing the inquiry form?",
        "e_learning_platform_owner": "If I share this report with our developer and our legal counsel, can you provide a brief written summary that explains the difference between the GDPR/CCPA compliance findings vs. the accessibility findings so they can divide the work appropriately?",
        "chiropractor_practice_owner": "If I fix the top local SEO and click-to-call issues this month, what's a realistic improvement in new patient inquiry calls I should expect before my next busy season?",
        "tech_startup_cto": "Can you give me the top 3 findings in a format I can paste into a Jira ticket with OWASP category, severity, and exact remediation step so my engineering team can start working on them today?",
        "spa_salon_owner": "Which single fix from this report would have the most direct impact on getting more client bookings from Google searches this month?",
        "real_estate_agent_owner": "If I can only prioritize two fixes before open house season, which two items from this report have the highest impact on organic lead generation and email security?",
        "franchise_expansion_buyer": "If I wanted to run this same audit for my other two locations, what would that process look like and how long would each assessment take?",
        "anxious_solopreneur": "Which one thing from this report can I do right now — this afternoon — using just my website platform's dashboard, with zero technical knowledge?",
        "nonprofit_executive_director": "Can you provide a one-paragraph written statement I can attach to a grant application as evidence that we have documented our accessibility gaps and have a remediation plan?",
        "tech_savvy_diy_owner": "Can you show me the exact HTTP response headers the scan captured so I can verify these findings myself against what I see in Chrome DevTools?",
        "cybersecurity_msp_prospect": "Can you walk me through what the onboarding process looks like for our first 5 client audits — what data do we provide, how long does each assessment take, and what do we deliver back to the client?",
        "interior_designer_owner": "If I could only fix 3 things from this report before my next major project presentation meeting next week, which three would have the most immediate impact on how a high-value client perceives my studio's website?",
    }
    return overflow.get(scenario_key, "What would the next step be over email?")


def _score_transcript(turns: list[dict[str, str]], *, report_highlights: list[str] | None = None) -> tuple[float, float, float]:
    full = "\n".join([f"{t['role']}: {t['text']}" for t in turns])
    low = full.lower()
    score_trust = 70.0
    score_close = 68.0
    score_objection = 68.0

    # Trust signals — evidence quality and specificity
    if "screenshot" in low and "roadmap" in low:
        score_trust += 10
    if "evidence" in low or "page-level" in low:
        score_trust += 5
    if "prioritized" in low or "priority" in low:
        score_trust += 4
    if "confidence" in low or "verified" in low:
        score_trust += 3
    if "privacy" in low or "no intrusive" in low:
        score_trust += 3

    # Objection-handling signals — value framing, ROI, urgency
    if "priority" in low and "impact" in low:
        score_objection += 8
    if "roi" in low or "return on" in low:
        score_objection += 5
    if "299" in low or "value" in low:
        score_objection += 4
    if "fix" in low and "today" in low:
        score_objection += 3
    if "remediation" in low or "actionable" in low:
        score_objection += 3

    # Close signals — forward momentum language
    if "next step" in low or "invoice" in low:
        score_close += 8
    if "deliverable" in low or "24 hour" in low or "24-hour" in low:
        score_close += 5
    if "ready" in low or "proceed" in low:
        score_close += 4
    if "developer" in low or "implement" in low:
        score_close += 3

    # Negative signals — off-channel or uncertainty
    if "call" in low or "zoom" in low or "meeting" in low:
        score_close -= 8   # agent drifting to a non-email channel
    if "not sure" in low or "can't guarantee" in low:
        score_trust -= 4

    # Vague/hedging language penalties — undermine trust and close rates
    vague_phrases = ["it depends", "could be", "might be", "generally speaking", "in most cases", "not always", "varies by"]
    vague_count = sum(1 for phrase in vague_phrases if phrase in low)
    if vague_count >= 2:
        score_trust -= vague_count * 2
        score_close -= vague_count

    # Specificity bonuses — concrete numbers and technical terms signal expertise
    if re.search(r'\b\d{1,3}\s*%|\$\d+|\b\d+\s+(?:days|hours|issues|pages|findings)\b', low):
        score_trust += 5
        score_objection += 4

    # Technical credibility — naming specific protocols and standards
    if any(term in low for term in ["dmarc", "spf", "dkim", "tls", "ssl", "wcag", "schema", "canonical"]):
        score_trust += 3
        score_objection += 2

    # Highlight specificity bonus — when agent replies reference specific finding titles from the
    # report, it demonstrates the conversation is grounded in real evidence rather than generic copy.
    if report_highlights:
        agent_text = " ".join(t["text"].lower() for t in turns if t.get("role") == "agent")
        hl_mentioned = sum(
            1 for hl in report_highlights
            if len(hl) > 8 and hl.lower()[:30] in agent_text
        )
        if hl_mentioned >= 2:
            score_trust += 4
            score_objection += 3
        elif hl_mentioned >= 1:
            score_trust += 2
            score_objection += 1

    return max(0, min(100, score_close)), max(0, min(100, score_trust)), max(0, min(100, score_objection))


def _match_highlights_to_persona(highlights: list[str], scenario_key: str) -> list[str]:
    """Reorder highlights so the most persona-relevant ones appear first (v20).

    Different personas care about different risk categories:
    - Compliance/legal personas: security and email auth findings first
    - Conversion-focused personas: conversion and performance findings first
    - SEO-focused personas: SEO findings first
    - Technical personas: high-severity findings in any category first (no reordering needed)
    - Default: preserve input order (caller already sorts by severity+confidence)

    The matching is keyword-based (finding titles are plain strings with no category tag),
    so we look for words that signal the relevant domain.
    """
    _SECURITY_SIGNALS = re.compile(
        r'\b(?:ssl|tls|cert|header|https|dmarc|spf|dkim|exposed|cookie|csp|cors|sri|phish|'
        r'injection|xss|sql|credential|password|auth|encrypt|vuln|cve)\b',
        re.IGNORECASE,
    )
    _CONVERSION_SIGNALS = re.compile(
        r'\b(?:cta|form|checkout|cart|conversion|trust|testimonial|chat|click.to.call|'
        r'video|social.proof|friction|lead|phone|copyright|pricing)\b',
        re.IGNORECASE,
    )
    _SEO_SIGNALS = re.compile(
        r'\b(?:seo|meta|title|h1|heading|sitemap|canonical|schema|keyword|index|'
        r'content|duplicate|redirect|thin|rating|description)\b',
        re.IGNORECASE,
    )
    _ADA_SIGNALS = re.compile(
        r'\b(?:ada|wcag|accessibility|aria|alt.text|label|iframe|skip.nav|landmark|'
        r'screen.reader|contrast|focus|keyboard)\b',
        re.IGNORECASE,
    )

    _COMPLIANCE_PERSONAS = {
        "compliance_cautious", "accessibility_attorney", "healthcare_compliance_buyer",
        "franchise_owner", "data_privacy_concerned", "cybersecurity_worried",
        "enterprise_it_manager", "budget_constrained_nonprofit", "gdpr_anxious_buyer",
        "cybersecurity_msp_prospect",
        "nonprofit_board_member",
        "dental_practice_owner",
        "legal_professional", "b2b_saas_founder", "budget_approval_needed",
        "insurance_agent_owner", "physical_therapist_owner", "accountant_practice_owner",
        "financial_advisor_owner",
        "optometry_practice_owner",
        "e_learning_platform_owner",
        "tech_startup_cto",
        "nonprofit_executive_director",
    }
    _CONVERSION_PERSONAS = {
        "ecommerce_cro_owner", "price_sensitive", "roi_focused_buyer", "quick_start_buyer",
    }
    _SEO_PERSONAS = {
        "seo_focused_buyer", "social_proof_seeker", "multi_location_owner", "local_seo_buyer",
        "restaurant_owner", "review_reputation_buyer", "home_services_owner",
        "fitness_studio_owner", "already_has_seo_agency",
        "childcare_provider_owner", "auto_repair_shop_owner", "veterinary_clinic_owner",
        "property_management_owner", "spa_salon_owner", "real_estate_agent_owner",
        "tutoring_center_owner", "boutique_hotel_owner",
        "photography_studio_owner",
        "landscaping_business_owner",
        "wedding_venue_owner",
        "chiropractor_practice_owner",
        "franchise_expansion_buyer",
        "anxious_solopreneur",
        "tech_savvy_diy_owner",
        "interior_designer_owner",
    }

    if scenario_key in _COMPLIANCE_PERSONAS:
        priority_re = re.compile(
            _SECURITY_SIGNALS.pattern + '|' + _ADA_SIGNALS.pattern, re.IGNORECASE
        )
    elif scenario_key in _CONVERSION_PERSONAS:
        priority_re = _CONVERSION_SIGNALS
    elif scenario_key in _SEO_PERSONAS:
        priority_re = _SEO_SIGNALS
    else:
        return highlights  # preserve caller's order for all other personas

    primary = [h for h in highlights if priority_re.search(h)]
    secondary = [h for h in highlights if not priority_re.search(h)]
    return primary + secondary


def preferred_persona_order(coverage: dict[str, int], pressure: dict[str, int] | None = None) -> list[str]:
    """Return scenario keys sorted by run count ascending + weakness pressure descending.

    Pass the ``persona_coverage`` dict from strategy memory to ensure that all
    personas are exercised evenly across iterations rather than relying on
    pure random selection.
    """
    pressure = pressure or {}
    return sorted(
        [s[0] for s in SCENARIOS],
        key=lambda k: (coverage.get(k, 0), -int(pressure.get(k, 0) or 0), k),
    )


def _turn_target_for_scenario(*, scenario_key: str, max_turn_count: int, persona_pressure: dict[str, int] | None = None) -> int:
    pressure = int((persona_pressure or {}).get(scenario_key, 0) or 0)
    bonus = min(2, max(0, pressure))
    return max(4, min(8, int(max_turn_count) + bonus))


def run_sales_simulation(
    *,
    settings: AgentSettings,
    business: SampledBusiness,
    report_highlights: list[str],
    preferred_personas: list[str] | None = None,
    scenario_count: int = 6,
    persona_pressure: dict[str, int] | None = None,
    max_turn_count: int = 5,
) -> list[SalesSimulationScenario]:
    rng = random.Random()
    target_count = max(1, min(len(SCENARIOS), int(scenario_count or 6)))

    if preferred_personas:
        # Prioritise personas that have been run least — guarantees full coverage across iterations
        pref_set = list(dict.fromkeys(preferred_personas))  # dedupe, preserve order
        scenario_map = {s[0]: s for s in SCENARIOS}
        prioritised = [scenario_map[k] for k in pref_set if k in scenario_map]
        rest = [s for s in SCENARIOS if s[0] not in set(pref_set)]
        rng.shuffle(rest)
        picked = (prioritised + rest)[:target_count]
    else:
        picked = SCENARIOS[:]
        rng.shuffle(picked)
        picked = picked[:target_count]
    base_turn_cap = max(4, min(8, int(max_turn_count or 5)))
    llm_budget = 10
    out: list[SalesSimulationScenario] = []

    clean_highlights = [h.replace("[axe] ", "").replace("[axe]", "").strip() for h in report_highlights if h.strip()]
    if not clean_highlights:
        clean_highlights = ["missing security headers", "SEO metadata gaps", "conversion friction"]
    opener = (
        f"Hi {business.contact_name.split()[0] if business.contact_name else 'there'}, I ran a web presence risk + growth assessment for {business.business_name}. "
        f"Top issues include: {', '.join(clean_highlights[:3])}."
    )

    for scenario_key, persona in picked:
        # Reorder highlights so the most persona-relevant findings appear first in the opener (v20)
        persona_highlights = _match_highlights_to_persona(clean_highlights, scenario_key)
        persona_opener = (
            f"Hi {business.contact_name.split()[0] if business.contact_name else 'there'}, I ran a web presence risk + growth assessment for {business.business_name}. "
            f"Top issues include: {', '.join(persona_highlights[:3])}."
        )
        turns: list[dict[str, str]] = [{"role": "agent", "text": persona_opener}]
        scenario_turn_cap = _turn_target_for_scenario(
            scenario_key=scenario_key,
            max_turn_count=base_turn_cap,
            persona_pressure=persona_pressure,
        )
        turn_count = rng.randint(4, scenario_turn_cap)
        for i in range(1, turn_count + 1):
            user_text = _user_turn_template(scenario_key, i)
            turns.append({"role": "client", "text": user_text})
            use_llm = llm_budget > 0
            agent_text = _agent_turn(
                turns,
                scenario=scenario_key,
                settings=settings,
                use_llm=use_llm,
                report_highlights=report_highlights,
            )
            if use_llm:
                llm_budget -= 1
            turns.append({"role": "agent", "text": agent_text})
        close, trust, objection = _score_transcript(turns, report_highlights=report_highlights)
        out.append(
            SalesSimulationScenario(
                scenario_key=scenario_key,
                persona=persona,
                turns=turns,
                score_close=close,
                score_trust=trust,
                score_objection=objection,
            )
        )
    return out
