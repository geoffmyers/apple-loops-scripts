"""Apple's metadata vocabularies for Apple Loops, taken from Apple's own loops.

The Loop Browser filters on exact strings. A value outside these lists is not
"a custom tag", it is a loop that no filter will ever show -- and a valid
category paired with a valid subcategory can still be a combination Apple
never ships, which is just as invisible.

These are NOT hand-curated. They are a census of all 34,928 loops in
macOS 27.0 /Library/Audio/Apple Loops, produced by
`scripts/validate-against-apple-loops.py --mode vocabulary`. The curated lists
they replaced were wrong in both directions: they invented values Apple has
never shipped ("Hi-Hat" where Apple writes "Hi-hat", "Male Vocal" where Apple
writes "Male", "Sound Effects", "Textures", "Other") and they listed 13 genres
where the library ships 30.

The raw census is in `reference/apple-loops-shipped-vocabulary.json`, and
`tests/test_apple_ground_truth.py` asserts these stay equal to it.

An empty subcategory is legitimate and common, so "" is always acceptable;
APPLE_FALLBACK_SUBCATEGORY is what to write when a source gives us nothing
more specific.
"""

# 16 categories.
APPLE_CATEGORIES = (
    "Bass", "Brass", "Drums", "Guitars", "Horn/Wind", "Keyboards",
    "Mallets", "Mixed", "Other", "Other Instrument", "Percussion",
    "Sound Effect", "Strings", "Texture/Atmosphere", "Vocals", "Woodwind",
)

# 82 subcategories. "" is also valid and is not listed here.
APPLE_SUBCATEGORIES = (
    "Accordion", "Acoustic Bass", "Acoustic Guitar", "Ambience",
    "Animals", "Bagpipe", "Banjo", "Bassoon", "Bell", "Bongo", "Celesta",
    "Cello", "Chime", "Choir", "Clarinet", "Clave", "Clavinet", "Conga",
    "Cowbell", "Cymbal", "Double Bass", "Drum Kit", "Electric Bass",
    "Electric Guitar", "Electric Piano", "Electronic Beats",
    "English Horn", "Explosions", "Female", "Flute", "Foley",
    "French Horn", "Gong", "Harmonica", "Harp", "Harpsichord", "Hi-hat",
    "Impacts & Crashes", "Kalimba", "Kick", "Koto", "Male", "Mandolin",
    "Marimba", "Mech/Tech", "Misc.", "Motions & Transitions", "Oboe",
    "Organ", "Other Instrument", "Pan Flute", "Pedal Steel Guitar",
    "People", "Piano", "Piccolo", "Rattler", "Recorder", "Saxophone",
    "Sci-Fi", "Shaker", "Shaker2", "Sitar", "Slide Guitar", "Snare",
    "Sports & Leisure", "Steel Drum", "Synthesizer", "Synthetic Bass",
    "Tambourine", "Timpani", "Tom", "Transportation", "Trombone",
    "Trumpet", "Tuba", "Vibraphone", "Vinyl/Scratch", "Viola", "Violin",
    "Weapons", "Work/Home", "Xylophone",
)

# 30 genres. Note how specific these are -- Apple ships "Deep House",
# "Tech House" and "Techno" separately rather than under one
# "Electronic/Dance" umbrella, so a source genre usually maps straight across.
APPLE_GENRES = (
    "Bass House", "Chillwave", "Chinese Traditional", "Cinematic/New Age",
    "Country/Folk", "Deep House", "Dubstep", "Electro House",
    "Electronic", "Electronic Pop", "Electronic/Dance", "Experimental",
    "Funk", "Future Bass", "Hip Hop", "Hip Hop/RnB", "House", "Indie",
    "Jazz", "Modern RnB", "Orchestral", "Other Genre", "Reggaeton Pop",
    "Rock/Blues", "Sound_Effects", "Tech House", "Techno", "Urban",
    "Vintage Breaks", "World/Ethnic",
)

# 18 descriptors.
APPLE_DESCRIPTORS = (
    "Acoustic", "Arrhythmic", "Cheerful", "Clean", "Dark", "Dissonant",
    "Distorted", "Dry", "Electric", "Ensemble", "Fill", "Grooving",
    "Intense", "Melodic", "Part", "Processed", "Relaxed", "Single",
)

# Which subcategories Apple actually pairs with each category.
APPLE_CATEGORY_SUBCATEGORIES = {
    "Bass": (
        "Acoustic Bass", "Electric Bass", "Synthetic Bass",
    ),
    "Brass": (
        "Trombone", "Trumpet", "Tuba",
    ),
    "Drums": (
        "Cymbal", "Drum Kit", "Electronic Beats", "Hi-hat", "Kick",
        "Snare", "Tom",
    ),
    "Guitars": (
        "Acoustic Guitar", "Banjo", "Electric Guitar", "Mandolin",
        "Other Instrument", "Pedal Steel Guitar", "Slide Guitar",
    ),
    "Horn/Wind": (
        "Bagpipe", "Bassoon", "Clarinet", "English Horn", "Flute",
        "French Horn", "Harmonica", "Oboe", "Other Instrument",
        "Pan Flute", "Piccolo", "Recorder", "Saxophone", "Trombone",
        "Trumpet", "Tuba",
    ),
    "Keyboards": (
        "Accordion", "Celesta", "Clavinet", "Electric Piano",
        "Harpsichord", "Organ", "Piano", "Synthesizer",
    ),
    "Mallets": (
        "Bell", "Kalimba", "Marimba", "Steel Drum", "Synthesizer",
        "Timpani", "Vibraphone", "Xylophone",
    ),
    "Mixed": (
        "Other Instrument",
    ),
    "Other": (
    ),
    "Other Instrument": (
        "Other Instrument", "Synthesizer",
    ),
    "Percussion": (
        "Bongo", "Chime", "Clave", "Conga", "Cowbell", "Electronic Beats",
        "Gong", "Kick", "Other Instrument", "Rattler", "Shaker",
        "Shaker2", "Synthesizer", "Tambourine", "Vinyl/Scratch",
    ),
    "Sound Effect": (
        "Ambience", "Animals", "Explosions", "Foley", "Impacts & Crashes",
        "Mech/Tech", "Misc.", "Motions & Transitions", "Other Instrument",
        "People", "Sci-Fi", "Sports & Leisure", "Synthesizer",
        "Transportation", "Weapons", "Work/Home",
    ),
    "Strings": (
        "Cello", "Double Bass", "Harp", "Koto", "Other Instrument",
        "Sitar", "Viola", "Violin",
    ),
    "Texture/Atmosphere": (
        "Electric Piano", "Other Instrument", "Synthesizer",
    ),
    "Vocals": (
        "Choir", "Female", "Male", "Other Instrument",
    ),
    "Woodwind": (
        "Clarinet", "Flute", "Pan Flute", "Recorder", "Saxophone",
    ),
}

# Shipped under most categories, so it is the safe "nothing more specific
# known" value. "Other" is a CATEGORY in Apple's data, never a subcategory.
APPLE_FALLBACK_SUBCATEGORY = "Other Instrument"

# Apple's key type values. "" is the most common of all; "any" also appears.
APPLE_KEY_TYPES = ("", "minor", "major", "both", "neither", "any")

# Categories Apple ships with no key signature.
PERCUSSIVE_APPLE_CATEGORIES = frozenset({"Drums", "Percussion"})
