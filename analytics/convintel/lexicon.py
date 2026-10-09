"""Word lists for the keyword layer (analytics/convintel/rules.py): English, romanized Hindi (Hinglish) and Devanagari.

Each category is a list of (regex, tag) pairs, most specific first: rules.py joins a category into one alternation,
so at any position the first listed term that matches wins ("fees kitni hai" before "fees"). rules.py matches the
lower-cased transcript (so terms are written in lower case), adds word boundaries that also work in Devanagari
(Python's ``\\b`` does not, because vowel signs are not word characters) and drops a negated match (analytics.
definitions' rule before it, or a Hindi "nahi"/"mat" right after it). A tag maps a match onto the schema's
vocabularies: SIGNAL_TYPES for buying, payment_intent and urgency; OBJECTION_CATEGORIES for objection and
course_availability; the keys of analytics.definitions.NEGATIVE_SIGNALS for negative. The shared buying and negative
patterns of analytics/definitions.py are imported rather than copied, so a fix there reaches this layer too.

Transcripts are machine-transcribed English with no speaker labels, so English and romanized Hindi carry most of
the weight; the Devanagari terms keep the layer working if the transcriber ever writes Hindi script.
"""

from __future__ import annotations

from analytics.definitions import BUYING_SIGNALS, NEGATIVE_SIGNALS

NAHI = r"(?:nahi|nahin|nhi)"
# Word characters: Python's \w misses Devanagari vowel signs, so the block is added (the dandas \u0964-5 are not).
WORD_CHARS = r"\w\u0900-\u0963\u0966-\u097f"
DW = rf"[{WORD_CHARS}]*"   # the rest of a word
# analytics.definitions keys -> schema SIGNAL_TYPES ("payment" sits in the payment_intent category instead).
DEFINITIONS_BUYING = {"fee": "fee_question", "emi_or_loan": "emi_interest", "decision_maker": "decision_maker_ready",
                      "start_date": "start_date", "enroll_intent": "enrol_intent", "refund_or_guarantee": "other"}

CATEGORIES: dict[str, list[tuple[str, str | None]]] = {
    "buying": [
        (r"(?:fees?|fee structure|price|cost|amount|charges?)\s+(?:kitn[aie]|kya)(?:\s+(?:hai|h|hogi|hoga|lagegi|lagega|padegi|padega))?", "fee_question"),
        (r"kitn[aie]\s+(?:fees?|paise|paisa|rupees?|rupaye|amount|charges?|lagega|lagegi|padega|padegi|hoga|hogi)", "fee_question"),
        (r"kitne\s+(?:ka|ki|ke)\s+(?:hai|h|course|padega|padegi)", "fee_question"),
        (r"fee structure|what (?:is|are|will be) the (?:fees?|price|cost|charges)", "fee_question"),
        (r"emi\s+(?:ho\s+ja(?:y|e)ega|hoga|ho\s+sakta|milega|milegi|mil\s+ja(?:y|e)ega|ka\s+option|option|available|possible|chahiye)", "emi_interest"),
        (r"(?:instal+ments?|kist|kisht)\s+(?:mein|me|main)", "emi_interest"),
        (r"(?:batch|course|class(?:es)?)\s+kab\s+(?:se\s+)?(?:start|shuru|chalu)\w*", "start_date"),
        (r"kab\s+(?:se\s+)?(?:start|shuru|chalu)\w*", "start_date"),
        (r"(?:join|enrol+|admission|register)\s+(?:karna|karni|karunga|karungi|kar\s+lunga|kar\s+lungi|kar\s+leta|kar\s+leti|lena|le\s+lunga|le\s+lungi|chahta|chahti)", "enrol_intent"),
        (r"i (?:want|would like|wish) to (?:join|enrol+|take (?:the|this) course|do (?:the|this) course)", "enrol_intent"),
        (r"(?:send|share|whatsapp|mail|email)\s+(?:me\s+)?(?:the\s+)?(?:details|brochure|syllabus|curriculum|information|info|pdf)", "documents_or_details"),
        (r"(?:details|brochure|syllabus|information)\s+(?:bhej|send|share|whatsapp)\w*", "documents_or_details"),
        (r"brochure|syllabus|curriculum", "documents_or_details"),
        (r"(?:ghar|family|parents|papa|mummy|husband|wife)\s+(?:wale\s+|walon\s+)?(?:maan|man)\s+gaye", "decision_maker_ready"),
        (r"interested", "other"),
        (r"कितनी फीस|कितने का|फीस", "fee_question"),
        (r"किस्त|ईएमआई", "emi_interest"),
        (r"कब (?:से )?(?:शुरू|स्टार्ट)" + DW, "start_date"),
        (r"(?:एडमिशन|दाखिला|जॉइन) (?:ले|कर)" + DW, "enrol_intent"),
        (r"ब्रोशर|सिलेबस|डिटेल्स? (?:भेज|शेयर)" + DW, "documents_or_details"),
        *[(BUYING_SIGNALS[k], tag) for k, tag in DEFINITIONS_BUYING.items()],
    ],
    "payment_intent": [
        (r"(?:pay|payment)\s+(?:kar|kr)\s*(?:dunga|dungi|doonga|doongi|deta|deti|denge|du|dun|raha|rahi|rahe|lunga|lungi|leta|leti)", "payment_intent"),
        (r"(?:send|share|bhej\w*)\s+(?:me\s+)?(?:the\s+)?(?:payment\s+)?link", "payment_intent"),
        (r"link\s+(?:bhej|send|share)\w*", "payment_intent"),
        (r"(?:aaj|kal|today|tomorrow)\s+(?:hi\s+)?(?:pay|payment)", "payment_intent"),
        (r"(?:token|booking|registration|seat booking)\s+(?:amount|fees?)|seat\s+block\w*", "payment_intent"),
        (r"upi|gpay|google pay|phone ?pe|paytm|neft|imps|net ?banking|debit card|credit card|scanner|qr code", "payment_intent"),
        (r"पेमेंट (?:कर|लिंक)" + DW + r"|भुगतान कर" + DW + r"|लिंक भेज" + DW, "payment_intent"),
        (BUYING_SIGNALS["payment"], "payment_intent"),
    ],
    "commitment": [
        (r"(?:i|we)(?:'ll| will| shall)\s+(?:definitely\s+|surely\s+|for sure\s+|also\s+|just\s+)?(?:join|enrol+|do it|take it|confirm|send|pay|register|complete|fill)", None),
        (r"(?:kar|kr|bhej|de|le)\s*(?:dunga|dungi|doonga|doongi|denge|lunga|lungi|lenge)", None),
        (r"pakka|final hai|it'?s a deal|count me in|let'?s do it|(?:i|we) promise", None),
        (r"पक्का|कर दूंगा|कर दूंगी|कर देंगे|भेज दूंगा|भेज दूंगी", None),
    ],
    "hesitation": [
        (r"let me (?:think|discuss|consult)|i(?:'ll| will) (?:think|let you know|get back)", None),
        (r"so+ch\s*(?:ke|kar|ka)?\s*(?:bata|btata|btati|batata|batati|bataunga|bataungi|bataenge|batayenge|btaunga)\w*", None),
        (r"so+ch(?:na|ni)?\s+(?:padega|padegi|hai|ke)|sochenge", None),
        (r"dekhte\s+(?:hain|hai|h)|dekh(?:ta|ti)\s+(?:hoon|hu|hun)", None),
        (r"(?<!ke )baad\s+(?:mein|me|main)|abhi\s+" + NAHI + r"|not now|maybe later", None),
        (r"(?:need|want|give me)\s+(?:some\s+)?time|(?:thoda|kuch)\s+time\s+(?:chahiye|do|dijiye|lagega)", None),
        (r"(?:maybe|probably|perhaps|i'll|i will|we'll|we will|i can|i would)\s+(?:\w+\s+){0,2}?(?:next|agle)\s+(?:month|year|mahine|saal)"
         r"|(?:next|agle)\s+(?:month|year|mahine|saal)\s+(?:dekh|soch|bata)\w*", None),
        (r"सोच (?:के|कर) बता" + DW + r"|सोचना पड़ेगा|सोचेंगे|बाद में|अभी नहीं", None),
    ],
    "uncertainty": [
        (r"(?:i'?m|i am) not sure|not sure|unsure|maybe|perhaps|i (?:don'?t|do not) know|confus\w+|doubts?", None),
        (r"pata\s+" + NAHI + r"|pta\s+" + NAHI + r"|shayad|kya pata|samajh\s+" + NAHI + r"\s+(?:aa|aaya|aayi)\w*", None),
        (r"पता नहीं|शायद|कन्फ्यूज" + DW, None),
    ],
    "urgency": [
        (r"urgent(?:ly)?|asap|as soon as possible|immediately|right away|today itself", "urgency"),
        (r"jald(?:i| se jald)|turant|aaj hi|abhi ke abhi", "urgency"),
        (r"जल्दी|तुरंत|आज ही", "urgency"),
        (r"(?:last|final) date|deadline|(?:by|before) (?:tonight|today)", None),
    ],
    "persuasion": [
        (r"limited seats?|(?:only|just) (?:\d+|few|a few) seats?|seats? (?:are )?(?:filling|limited|left)", None),
        (r"(?:special|exclusive|limited[- ]time) (?:offer|discount|price)|offer (?:valid|ends|is valid|till)", None),
        (r"discount|scholarship|waiver|cashback|early bird", None),
        (r"(?:price|fees?) (?:will )?(?:increase|go up|badh)\w*", None),
        (r"(?:don'?t|do not) miss|best (?:time|opportunity|chance)|(?:trust|believe) me|guaranteed?", None),
        (r"(?:sirf|only) (?:aaj|today)|aaj (?:tak|hi) (?:ka|ki) offer", None),
        (r"ऑफर|डिस्काउंट|छूट", None),
    ],
    "ineffective_wording": [
        (r"sorry (?:to|for) (?:disturb|bother)\w*|(?:i'?m|i am) (?:just|only) calling|just calling", None),
        (r"no pressure|if you (?:want|wish|like)|whenever you (?:want|are free|feel like)|(?:as|whatever) you (?:wish|want|like)", None),
        (r"aapki (?:marzi|marji|ichha|icchha)|jaisa (?:aap|aapko) (?:theek|thik|sahi) lage", None),
        (r"basically|to be (?:honest|frank)|honestly|kind of|sort of|i (?:guess|suppose)", None),
        (r"हो सके तो|आपकी मर्ज़ी|आपकी मर्जी", None),
    ],
    "objection": [
        (r"(?:too|very|bahut|bahot|kaafi|kafi|thoda)\s+(?:expensive|costly|high|m[ae]h[ae]?nga)", "price"),
        (r"(?:fees?|price|cost|amount)\s+(?:is\s+)?(?:too\s+|very\s+|bahut\s+|thoda\s+)?(?:high|zyada|jyada|jada)", "price"),
        (r"expensive|costly|m[ae]h[ae]?nga|out of (?:my )?budget|budget\s+(?:" + NAHI + r"|kam|low|issue|problem|constraint)", "price"),
        (r"(?:can'?t|cannot|can not|won'?t be able to) afford|afford\s+" + NAHI, "price"),
        (r"(?:discount|kam)\s+(?:kar|kr)\s*(?:do|dijiye|sakte|skte)", "price"),
        (r"pais[ae]\s+" + NAHI + r"|paison ki (?:dikkat|problem|kami)", "emi_or_finance"),
        (r"(?:no|don'?t have|do not have|not having) (?:the )?(?:money|funds)", "emi_or_finance"),
        (r"(?:money|financial|finance|funds?)\s+(?:issue|problem|crunch|constraint|dikkat|kami|tight)", "emi_or_finance"),
        (r"salary\s+(?:aane|aayegi|ke baad|comes|credit)\w*|after (?:my |the )?salary", "emi_or_finance"),
        (r"(?:emi|loan)\s+(?:" + NAHI + r"|not)\s+(?:ho|mil|possible|available)\w*", "emi_or_finance"),
        (r"(?:i'?m|i am|bahut|very|too|abhi)\s+busy|busy\s+(?:hu|hoon|hun|schedule)", "time"),
        (r"(?:no|don'?t have|do not have|not getting) (?:the |any )?time|time\s+(?:" + NAHI + r"|issue|problem|constraint)", "time"),
        (r"(?:can'?t|cannot) (?:manage|give) (?:the )?time", "time"),
        (r"is it worth|not worth|waste of (?:money|time)|what (?:will|would) i (?:get|gain)", "value_doubt"),
        (r"kya (?:fayda|faayda|fayeda)|(?:fayda|faayda) (?:kya|" + NAHI + r")|(?:isse|is se|isme|ismein|usse) kya milega|(?:available|free) on youtube|youtube (?:pe|par|mein|me) (?:free|mil)\w*", "value_doubt"),
        (r"fraud|scam|fake|cheat\w*|(?:don'?t|do not|can'?t|cannot) trust|trust\s+(?:issue|" + NAHI + r")|(?:bharosa|vishwas)\s+" + NAHI, "trust"),
        (r"is (?:it|this|the certificate|the course) (?:genuine|legit|real|valid|recogni[sz]ed|approved)|(?:recogni[sz]ed|valid|approved) (?:hai|h) kya", "trust"),
        (r"(?:ask|check with|consult|discuss with|talk to)\s+(?:my\s+)?(?:parents?|father|mother|dad|mom|husband|wife|family|papa|mummy|brother|sister)", "family_approval"),
        (r"(?:ghar|family|parents|papa|mummy|husband|wife|bhai|pati|patni)\s+(?:pe|par|mein|me|se|walon se|walo se)\s+(?:puch|pooch|baat)\w*", "family_approval"),
        (r"puchna (?:padega|padegi|hoga)|(?:need|take|get|ask for|asking for) (?:the |my |their )?permission"
         r"|permission\s+(?:lena|leni|chahiye|from|" + NAHI + r")", "family_approval"),
        (r"not (?:relevant|useful|suitable|meant) for me|not for me|(?:different|other) (?:field|background|profession)", "course_fit"),
        (r"not (?:my|in my) (?:field|area|domain)|(?:too|very) (?:basic|advanced)|non[- ]law background", "course_fit"),
        (r"(?:mere|mujhe) (?:kaam|liye) ka " + NAHI + r"|mere (?:field|kaam) se (?:related )?" + NAHI, "course_fit"),
        (r"(?:kahin|kahi) aur (?:se )?(?:join|kar|le)\w*|dusr[ie] (?:jagah|institute|company)", "joined_elsewhere"),
        (r"(?:already|pehle (?:hi|se)) (?:join|enrol+|admission|taken (?:an )?admission)\w*", "joined_elsewhere"),
        (r"(?:job|placement)\s+(?:guarantee|assurance|milegi|milega|hogi)|will i get (?:a )?(?:job|placement)|no placement", "job_or_placement"),
        (r"(?:speak|talk|explain|baat karo|bolo|samjhao)\s+(?:in\s+)?hindi|hindi (?:mein|me|main) (?:baat|bol|samjha)\w*", "language"),
        (r"english\s+(?:is\s+)?(?:not good|weak|difficult|kamzor)|english\s+" + NAHI + r"\s+(?:aati|aata)|language (?:issue|problem|barrier)", "language"),
        (r"(?:no|don'?t have|do not have|not having) (?:a )?(?:laptop|computer|smartphone)|laptop\s+" + NAHI, "technical"),
        (r"(?:network|internet|signal|connection)\s+(?:issue|problem|" + NAHI + r"|weak|slow|is weak|is bad|down)", "technical"),
        (r"(?:can'?t|cannot|not able to) hear|(?:awaa?z|voice)\s+(?:" + NAHI + r"|not|break|cut)\w*", "technical"),
        (r"interest(?:ed)?\s+" + NAHI + r"|(?:mujhe|hume|humein|hamein|ye|yeh|course)\s+" + NAHI + r"\s+(?:chahiye|karna)", "not_interested"),
        (r"(?:i|we) (?:don'?t|do not) need (?:it|this|that|the course|any course|this course)", "not_interested"),
        (r"महंगा|मंहगा|महँगा|फीस (?:ज्यादा|ज़्यादा|बहुत)", "price"),
        (r"पैसे नहीं|पैसा नहीं", "emi_or_finance"),
        (r"समय नहीं|टाइम नहीं", "time"),
        (r"क्या (?:फायदा|फ़ायदा)|(?:फायदा|फ़ायदा) (?:क्या|नहीं)", "value_doubt"),
        (r"(?:भरोसा|विश्वास) नहीं|फ्रॉड", "trust"),
        (r"घर (?:पे|पर|में) पूछ" + DW + r"|पूछना पड़ेगा", "family_approval"),
        (r"दूसरी जगह", "joined_elsewhere"),
        (r"(?:नौकरी|जॉब|प्लेसमेंट) (?:मिलेगी|मिलेगा|की गारंटी|गारंटी)", "job_or_placement"),
        (r"हिंदी में (?:बात|बोल|समझा)" + DW, "language"),
        (r"नेटवर्क (?:नहीं|कमज़ोर|कमजोर|प्रॉब्लम|की (?:दिक्कत|समस्या))", "technical"),
        (r"रुचि नहीं|इंटरेस्ट नहीं|(?:मुझे|हमें|ये|यह) नहीं चाहिए", "not_interested"),
        (NEGATIVE_SIGNALS["joined_elsewhere"], "joined_elsewhere"),
        (NEGATIVE_SIGNALS["not_interested"], "not_interested"),
    ],
    "payment_friction": [
        (r"payment\s+(?:failed|fail|declined|stuck|pending|issue|problem|not (?:going|done|working|successful))", None),
        (r"payment\s+" + NAHI + r"\s+(?:ho|hua|ja)\w*|transaction\s+(?:failed|declined|" + NAHI + r")\w*", None),
        (r"link\s+(?:not working|expired|(?:is not|isn'?t) working|" + NAHI + r" (?:khul|chal)\w*|open " + NAHI + r")", None),
        (r"otp\s+(?:not|" + NAHI + r")\s*\w*|(?:card|upi)\s+(?:declined|failed|not working|limit)|limit (?:exceeded|cross)\w*", None),
        (r"(?:emi|loan)\s+(?:rejected|not approved|declined|reject)\w*|cibil|credit score", None),
        (r"(?:paise|amount|money)\s+(?:kat|cut|deduct)\w*|(?:no|don'?t have a|do not have a) credit card|credit card\s+" + NAHI, None),
        (r"(?:gpay|phone ?pe|paytm|google pay)\s+(?:not working|" + NAHI + r" chal)\w*", None),
        (r"पेमेंट (?:नहीं|फेल)" + DW + r"|लिंक नहीं खुल" + DW, None),
    ],
    "course_availability": [
        (r"(?:batch|seats?)\s+(?:is\s+|are\s+)?(?:full|closed|filled|over|band)", "course_unavailable"),
        (r"(?:admissions?|registrations?|enrol+ments?)\s+(?:is\s+|are\s+)?(?:closed|band|over)", "course_unavailable"),
        (r"course\s+(?:is\s+)?(?:not available|discontinued|closed|band|no longer (?:available|offered|running))", "course_unavailable"),
        (r"(?:we|they) (?:don'?t|do not) (?:have|offer) (?:that|this|such a|any such) (?:course|program)", "course_unavailable"),
        (r"(?:no|koi) (?:such|aisa) (?:course|program)|(?:that|this|yeh|ye|wo|woh) course\s+" + NAHI + r"\s+(?:hai|h|milega|available)", "course_unavailable"),
        (r"बैच (?:फुल|बंद)|कोर्स (?:उपलब्ध नहीं|बंद)", "course_unavailable"),
    ],
    "callback": [
        (r"call\s+(?:me\s+)?(?:back|later|tomorrow|kal|after|in the (?:evening|morning|afternoon))|call\s?back", None),
        (r"(?:baad|bad)\s+(?:mein|me|main)\s+(?:call|phone|baat)|(?:kal|parso|shaam|sham|subah)\s+(?:ko\s+)?(?:call|phone|baat)", None),
        (r"(?:call|phone)\s+(?:kar|kr)\s*(?:na|ni|iye|iyega|unga|ungi|ta|ti|enge|lena|lo|dena|do)", None),
        (r"(?:i|we)(?:'ll| will) (?:call|ring) (?:you|back)|(?:free|busy) (?:hoke|hokar|ho kar) (?:call|baat)", None),
        (r"(?:बाद में|कल) (?:कॉल|फोन|बात)|कॉल (?:करना|करूंगा|करूंगी|करेंगे|कीजिए)|वापस कॉल", None),
    ],
    "negative": [
        *[(rx, key) for key, rx in NEGATIVE_SIGNALS.items()],
        (r"interest(?:ed)?\s+" + NAHI + r"|(?:mujhe|hume|humein)\s+" + NAHI + r"\s+(?:chahiye|karna)|call mat (?:karo|karna|kijiye)", "not_interested"),
        (r"रुचि नहीं|इंटरेस्ट नहीं|कॉल मत", "not_interested"),
        (r"(?:kahin|kahi) aur (?:se )?(?:join|kar|le)\w*|दूसरी जगह", "joined_elsewhere"),
        (r"pais[ae]\s+" + NAHI + r"\s+(?:hai|h|hain)|afford\s+" + NAHI + r"|पैसे नहीं", "no_budget"),
        (r"galat (?:number|nambar)|(?:maine|mai ne) (?:koi )?(?:enquiry|form) " + NAHI + r"|गलत नंबर", "wrong_person"),
    ],
}
# The negative terms are negations themselves ("not interested"), so a "not" before them must not cancel them
# (analytics.lead_priority reads NEGATIVE_SIGNALS the same way).
NEGATABLE = frozenset(CATEGORIES) - {"negative"}
SIGNAL_CATEGORIES = ("buying", "payment_intent", "urgency")
OBJECTION_SOURCES = ("objection", "course_availability")

# Romanized Hindi function words (none is also a common English word) and English words, for language detection.
HINGLISH_WORDS = frozenset(
    "hai hain hoon hun nahi nahin nhi kya kaise kitna kitni kitne kab kahan kaun kyun kyon aap aapka aapki aapko mera "
    "meri mujhe hum humein hamein haan haanji accha acha achha theek thik karna karo kariye kijiye raha rahi rahe wala "
    "wali bhi toh mein baat bolo bataiye batao chahiye sakta sakti sakte hoga hogi abhi paise paisa ghar bhai yaar "
    "bilkul matlab lekin kyunki matlab samajh jayega jaega dunga dungi lunga lungi ji".split())
ENGLISH_WORDS = frozenset(
    "the is are was were you your we our they this that what when where which how why will would can could should "
    "have has had do does did and but because for with from about not yes okay ok please thank thanks sorry sir madam "
    "course fees fee payment link batch class classes emi call time details program certificate diploma".split())
QUESTION_START = r"(?:what|when|where|why|who|which|how|can you|could you|would you|will you|do you|does|did you|are you|is it|is there|kya|kitn[aie]|kab|kaise|kahan|kaun|kyun|kyon|क्या|कब|कैसे|कितन" + DW + ")"
QUESTION_END = r"(?:kya|क्या)"
# Day and time words for a dated next step ("kal shaam 5 baje", "Monday at 11").
TIME_WORDS = (r"\d{1,2}(?::\d{2})?\s*(?:baje|bje|am|pm|a\.m\.|p\.m\.|o'?clock)|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*"
              r"|tomorrow|day after tomorrow|tonight|kal|parso|parson|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
              r"|somvar|mangalvar|budhvar|guruvar|shukravar|shanivar|ravivar|weekend|next week|agle hafte"
              r"|subah|shaam|sham|dopahar|कल|परसों|सुबह|शाम|बजे")
# A payment step said in Hinglish, alongside analytics.call_markers' English one.
PAYMENT_STEP_HINGLISH = (r"link\s+(?:bhej|send|share)\w*|(?:bhej|send|share)\w*\s+(?:\w+\s+){0,2}link|payment\s+link"
                         r"|(?:upi|gpay|phone ?pe|paytm)\s+(?:kar|kr)\s*(?:do|dijiye|dena|de)|scanner\s+bhej\w*|qr\s+code"
                         r"|account\s+(?:number|details)\s+bhej\w*|लिंक भेज" + DW)
# Course names: "diploma in X", "certificate course on X", "X bootcamp", "X ka course".
COURSE_LEADS = r"(?:executive\s+)?(?:diploma|certificate(?:\s+course)?|certification|course|program(?:me)?|masterclass|training)"
COURSE_KINDS = r"boot\s?camp|master\s?class"
# A course name ends at a stop word; joining words may sit inside one ("law and paralegal studies") but not at its ends.
COURSE_STOP = frozenset(
    "is are was were will would which that where so but because please sir madam maam mam then also right okay ok "
    "with from at for you your it this we i to our my their or about join ye yeh wo woh aapka mera ek ka ki ke "
    "hai bhi".split())
COURSE_JOIN = frozenset("and & of the in on a an".split())
AMOUNT_UNITS = {"k": 1_000, "thousand": 1_000, "hazar": 1_000, "hazaar": 1_000, "hajar": 1_000, "hajaar": 1_000,
                "हज़ार": 1_000, "हजार": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
                "लाख": 100_000, "crore": 10_000_000, "crores": 10_000_000, "करोड़": 10_000_000}
