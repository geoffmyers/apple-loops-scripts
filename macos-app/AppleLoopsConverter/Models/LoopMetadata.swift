import Foundation

enum LoopCategory: String, CaseIterable, Identifiable {
    case bass = "Bass"
    case drums = "Drums"
    case guitars = "Guitars"
    case hornWind = "Horn/Wind"
    case keyboards = "Keyboards"
    case mallets = "Mallets"
    case mixed = "Mixed"
    case otherInstrument = "Other Instrument"
    case percussion = "Percussion"
    case soundEffect = "Sound Effect"
    case strings = "Strings"
    case textureAtmosphere = "Texture/Atmosphere"
    case vocals = "Vocals"

    var id: String { rawValue }
}

enum LoopGenre: String, CaseIterable, Identifiable {
    case cinematicNewAge = "Cinematic/New Age"
    case countryFolk = "Country/Folk"
    case electronicDance = "Electronic/Dance"
    case experimental = "Experimental"
    case funk = "Funk"
    case hipHop = "Hip Hop"
    case jazz = "Jazz"
    case modernRnB = "Modern RnB"
    case orchestral = "Orchestral"
    case otherGenre = "Other Genre"
    case rockBlues = "Rock/Blues"
    case urban = "Urban"
    case worldEthnic = "World/Ethnic"

    var id: String { rawValue }
}

enum LoopDescriptor: String, CaseIterable, Identifiable {
    case acoustic = "Acoustic"
    case arrhythmic = "Arrhythmic"
    case cheerful = "Cheerful"
    case clean = "Clean"
    case dark = "Dark"
    case dissonant = "Dissonant"
    case distorted = "Distorted"
    case dry = "Dry"
    case electric = "Electric"
    case ensemble = "Ensemble"
    case fill = "Fill"
    case grooving = "Grooving"
    case intense = "Intense"
    case melodic = "Melodic"
    case part = "Part"
    case processed = "Processed"
    case relaxed = "Relaxed"
    case single = "Single"

    var id: String { rawValue }
}

enum KeySignature: String, CaseIterable, Identifiable {
    case a = "A"
    case aSharp = "A#"
    case b = "B"
    case c = "C"
    case cSharp = "C#"
    case d = "D"
    case dSharp = "D#"
    case e = "E"
    case f = "F"
    case fSharp = "F#"
    case g = "G"
    case gSharp = "G#"

    var id: String { rawValue }
}

enum KeyType: String, CaseIterable, Identifiable {
    case major = "Major"
    case minor = "Minor"
    case neither = "Neither"

    var id: String { rawValue }
}

enum TimeSignature: String, CaseIterable, Identifiable {
    case fourFour = "4/4"
    case threeFour = "3/4"
    case sixEight = "6/8"
    case twoFour = "2/4"
    case fiveFour = "5/4"
    case sevenEight = "7/8"

    var id: String { rawValue }

    var numerator: Int {
        switch self {
        case .fourFour: return 4
        case .threeFour: return 3
        case .sixEight: return 6
        case .twoFour: return 2
        case .fiveFour: return 5
        case .sevenEight: return 7
        }
    }

    var denominator: Int {
        switch self {
        case .fourFour, .threeFour, .twoFour, .fiveFour: return 4
        case .sixEight, .sevenEight: return 8
        }
    }
}

struct LoopMetadata: Equatable {
    var tempo: Double?
    var key: KeySignature?
    var keyType: KeyType = .major
    var category: LoopCategory?
    var subcategory: String = ""
    var genre: LoopGenre?
    var descriptors: Set<LoopDescriptor> = []
    var timeSignature: TimeSignature = .fourFour
    var beatCount: Int?

    var isAutoDetectedTempo: Bool = true
    var isAutoDetectedKey: Bool = true
    var isAutoDetectedCategory: Bool = true
    var isAutoDetectedGenre: Bool = true

    static let empty = LoopMetadata()
}
