import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { describe, it } from "node:test";
import { ANONYMIZER_VERSION, PII_TYPES, anonymize, replaceSpans } from "../anonymize/index.js";
import { lexicon } from "../anonymize/lexicon.js";
import { findPlaces, isSentenceStart, resolvePlaces } from "../anonymize/places.js";

// Cases shared with the Python anonymizer (tests/test_anonymizer_cases.py).
const CASES = JSON.parse(
  readFileSync(new URL("../../tests/fixtures/anonymizer_cases.json", import.meta.url), "utf8"),
);

function masked(text) {
  return anonymize(text).spans.map((s) => [s.type, text.slice(s.start, s.end)]);
}

function checkInvariants(text, result) {
  const { spans } = result;
  assert.ok(spans.every((s) => s.start >= 0 && s.start < s.end && s.end <= text.length));
  assert.ok(spans.every((s, k) => k === 0 || spans[k - 1].end <= s.start), "sorted and disjoint");
  assert.ok(spans.every((s) => PII_TYPES.includes(s.type)));
  assert.equal(replaceSpans(text, spans), result.text);
  assert.equal(Object.values(result.counts).reduce((a, b) => a + b, 0), spans.length);
}

const label = (text) => JSON.stringify(text.slice(0, 60));

describe("shared cases: must mask", () => {
  for (const c of CASES.must_mask) {
    it(label(c.text), { skip: c.python_only ? `python only: ${c.python_only}` : false }, () => {
      const result = anonymize(c.text);
      assert.deepEqual(masked(c.text), c.spans);
      if (c.output !== undefined) assert.equal(result.text, c.output);
      checkInvariants(c.text, result);
    });
  }
});

describe("shared cases: must not mask", () => {
  for (const c of CASES.must_not_mask) {
    it(label(c.text), () => {
      const result = anonymize(c.text);
      assert.deepEqual(result.spans, []);
      assert.equal(result.text, c.text);
    });
  }
});

describe("contract", () => {
  it("returns text, spans, counts and version", () => {
    const result = anonymize("falei com o João, meu cpf é 123.456.789-00");
    assert.deepEqual(result, {
      text: "falei com o [PESSOA], meu cpf é [CPF]",
      spans: [
        { start: 12, end: 16, type: "PESSOA", source: "gazetteer" },
        { start: 28, end: 42, type: "CPF", source: "regex" },
      ],
      counts: { CPF: 1, PESSOA: 1 },
      version: ANONYMIZER_VERSION,
    });
    assert.equal(ANONYMIZER_VERSION, "edge-1.1.0");
  });

  it("returns blank and missing text unchanged", () => {
    for (const text of ["", "   ", "\n"]) {
      assert.deepEqual(anonymize(text), { text, spans: [], counts: {}, version: ANONYMIZER_VERSION });
    }
    assert.equal(anonymize(undefined).text, "");
  });

  it("loads the IBGE lists and the allowlist", () => {
    assert.ok(lexicon.firstNames.size >= 3000 && lexicon.surnames.size >= 1500);
    assert.ok(lexicon.firstNames.has("joao") && lexicon.surnames.has("silva"));
    assert.ok(lexicon.isAllowed("Talvana") && lexicon.isAllowed("Pessoas e Cultura") && lexicon.isAllowed("RH"));
    assert.ok(lexicon.isAllowed("Março") && !lexicon.isAllowed("Marco"));
  });

  it("loads the IBGE place lists, classes, stoplist and cues", () => {
    assert.ok(lexicon.places.size > 9000);
    for (const key of ["sao paulo", "embu das artes", "sao jose dos campos", "jardim angela", "copacabana"]) {
      assert.ok(lexicon.places.has(key), key);
    }
    for (const label of ["zona 01", "primeiro subdistrito", "centro", "sede"]) assert.ok(!lexicon.places.has(label), label);
    assert.ok([...lexicon.placeStoplist].every((key) => lexicon.places.has(key)));
    assert.equal(lexicon.placeClass("saude"), "common");
    assert.equal(lexicon.placeClass("palmas"), "mixed");
    assert.equal(lexicon.placeClass("guarulhos"), "proper");
    for (const cue of ["moro em", "sou de", "transferido para", "filial de", "bairro", "em"]) {
      assert.ok(lexicon.placeCues.has(cue), cue);
    }
    const lengths = lexicon.placeIndex.get("sao");
    assert.ok(lengths[0] >= 4 && lengths.every((n, k) => k === 0 || lengths[k - 1] > n));
  });

  it("keeps the invariants on random mixtures", () => {
    const fragments = [
      "o joao", "Maria das Graças", "cpf 123.456.789-00", "(11) 98765-4321", "RH", "Talvana",
      "Rua Augusta, 1200", "placa ABC1D23", "a@b.com", "13º", "R$ 2.500,00", "Clara", "rosa",
      "matrícula 45821", "cep 01310-100", "São Paulo", "@ana", "12345678", ",", ".", "\n", "!",
      "não aguento mais", "SAP", "segunda-feira", "Seu Zé", "MG-12.345.678", "rg", "o gerente Antunes",
      "falei com a Kowalski", "😀", "moro em", "São José dos Campos", "Natal", "pirituba", "Marília",
      "Embu-Guaçu", "d'Água", "em", "sou de",
    ];
    let seed = 7;
    const random = () => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed / 2147483648;
    };
    for (let n = 0; n < 300; n += 1) {
      const size = 1 + Math.floor(random() * 12);
      const text = Array.from({ length: size }, () => fragments[Math.floor(random() * fragments.length)]).join(" ");
      checkInvariants(text, anonymize(text));
    }
  });
});

describe("context rule (in place of NER)", () => {
  // No name starts with a word of the IBGE first-name list, so only the context rule finds them.
  const people = [
    ["o gerente Antunes grita com todo mundo", [["PESSOA", "Antunes"]]],
    ["a supervisora Kowalski Brandão some", [["PESSOA", "Kowalski Brandão"]]],
    ["meu colega Deyvison me ajuda", [["PESSOA", "Deyvison"]]],
    ["meu colega Uelinton me ajuda", [["PESSOA", "Uelinton"]]],
    ["falei com o Kowalski e nada mudou", [["PESSOA", "Kowalski"]]],
    ["reclamei pra Jaqueliny ontem", [["PESSOA", "Jaqueliny"]]],
    ["o motorista Edivanilson atrasa", [["PESSOA", "Edivanilson"]]],
    ["Sr. Kowalski não deixa", [["PESSOA", "Kowalski"]]],
    // Two words that no list knows at most, then only surnames and first names from the lists.
    ["o gerente Antunes Kowalski Brandão some", [["PESSOA", "Antunes Kowalski Brandão"]]],
    ["meu colega Deyvison Kowalski Souza da Silva saiu", [["PESSOA", "Deyvison Kowalski Souza da Silva"]]],
    ["o gerente Antunes Kowalski Mandou embora", [["PESSOA", "Antunes Kowalski"]]],
  ];
  for (const [text, expected] of people) {
    it(`masks ${label(text)}`, () => {
      assert.deepEqual(masked(text), expected);
      assert.deepEqual(anonymize(text).spans.map((s) => s.source), expected.map(() => "context"));
    });
  }

  const kept = [
    "o gerente Geral não aparece",
    "O Gerente Regional não ouve ninguém.",
    "a coordenadora do Financeiro saiu",
    "o diretor Comercial cobra meta",
    "falei com o RH e com a Diretoria",
    "liguei pro Suporte e falei com a TI",
    "o gerente Sênior e o analista Pleno",
    "o Bradesco paga o salário em dia",
    "Clara falta de respeito",
    "conversei com a Talvana sobre isso",
    "o gestor Março passado mudou",
  ];
  for (const text of kept) {
    it(`keeps ${label(text)}`, () => assert.deepEqual(masked(text), []));
  }
});

describe("places (official IBGE lists)", () => {
  const found = (text) => findPlaces(text).map((p) => text.slice(p.start, p.end));
  // Same cases as tests/test_anonymize_places.py::test_find_places.
  const cases = [
    ["Contagem é longe", []],
    ["moro em Contagem", ["Contagem"]],
    ["moro em contagem", []],
    ["Perus é longe", []],
    ["a linha de Perus atrasa", ["Perus"]],
    ["Guarulhos é longe", ["Guarulhos"]],
    ["o pessoal de guarulhos", []],
    ["trabalho em guarulhos", ["guarulhos"]],
    ["Campo Grande é longe", []],
    ["o pessoal de Campo Grande", ["Campo Grande"]],
    ["moro em campo grande", []],
    ["moro em belo horizonte", ["belo horizonte"]],
    ["o Natal foi bom", []],
    ["moro em natal", []],
    ["sou de Natal", ["Natal"]],
    ["Feliz Natal", []],
    ["em marco tem PLR", []],
    ["moro em sousa", []],
    ["moro em Sousa", ["Sousa"]],
    ["moro em São José\ndos Campos", ["São José", "Campos"]],
    ["sou de Embu Guaçu", ["Embu Guaçu"]],
    ["sou de Olho d\u2019Água das Flores", ["Olho d\u2019Água das Flores"]],
    ["trabalho na Matriz", []],
    ["trabalho na Câmara", []],
    ["moro em\npirituba", []],
    ["moro em pirituba", ["pirituba"]],
  ];
  for (const [text, expected] of cases) {
    it(`finds ${label(text)}`, () => assert.deepEqual(found(text), expected));
  }

  const span = (text, part, type = "PESSOA", source = "gazetteer") => {
    const start = text.indexOf(part);
    return { start, end: start + part.length, type, source };
  };
  // Same cases as tests/test_anonymize_places.py::test_resolve.
  const overlaps = [
    ["a Ana Carolina chegou", ["Ana Carolina"], [["PESSOA", "Ana Carolina"]]],
    ["moro em Marília", ["Marília"], [["LOCAL", "Marília"]]],
    ["o pessoal de São José dos Campos", ["José dos Campos"], [["LOCAL", "São José dos Campos"]]],
    ["a Marília do RH", ["Marília"], [["PESSOA", "Marília"]]],
    ["falei com o Barbosa", [], [["PESSOA", "Barbosa"]]],
    ["o pessoal de Jundiaí", [], [["LOCAL", "Jundiaí"]]],
  ];
  for (const [text, names, expected] of overlaps) {
    it(`resolves ${label(text)}`, () => {
      const spans = resolvePlaces(names.map((n) => span(text, n)), findPlaces(text), text);
      assert.deepEqual(spans.map((s) => [s.type, text.slice(s.start, s.end)]), expected);
    });
  }

  it("lets context spans without known names give way to places", () => {
    for (const [text, part, type] of [["liguei pra Jundiaí", "Jundiaí", "LOCAL"], ["liguei pra Souza", "Souza", "PESSOA"]]) {
      const spans = resolvePlaces([span(text, part, "PESSOA", "context")], findPlaces(text), text);
      assert.deepEqual(spans.map((s) => s.type), [type], text);
    }
  });

  it("knows where a sentence starts", () => {
    assert.ok(isSentenceStart("Guarulhos", 0));
    assert.ok(isSentenceStart("Fim. \"Guarulhos", 6));
    assert.ok(isSentenceStart("fim\n- Guarulhos", 6));
    assert.ok(!isSentenceStart("de Guarulhos", 3));
  });

  it("drops places inside a regex span", () => {
    assert.deepEqual(masked("Mande para sao.paulo@talvana.com.br"), [["EMAIL", "sao.paulo@talvana.com.br"]]);
    assert.deepEqual(masked("Rua Guarulhos, 45"), [["LOCAL", "Rua Guarulhos, 45"]]);
  });
});

describe("names with an elided prefix", () => {
  const people = [
    ["a gerente Joana D'Arc grita", [["PESSOA", "Joana D'Arc"]]],
    ["o gerente D'Ávila grita", [["PESSOA", "D'Ávila"]]],
    ["o gerente Dall'Agnol grita", [["PESSOA", "Dall'Agnol"]]],
    ["Sant\u2019Anna me ajudou", [["PESSOA", "Sant\u2019Anna"]]],
    ["@ab'Paulo", [["PESSOA", "@ab"], ["PESSOA", "Paulo"]]],
  ];
  for (const [text, expected] of people) {
    it(`masks ${label(text)}`, () => assert.deepEqual(masked(text), expected));
  }
  it("keeps ordinary words", () => {
    for (const text of ["um copo d'água e um pingo d'Água", "don't stop", "o McDonald's da esquina"]) {
      assert.deepEqual(masked(text), [], text);
    }
  });
});

describe("decomposed accents (NFD)", () => {
  it("masks names and places written with combining marks", () => {
    const cases = [
      ["Joa\u0303o me ajuda", "[PESSOA] me ajuda"],
      ["o gerente Joa\u0303o grita", "o gerente [PESSOA] grita"],
      ["a Mo\u0301nica e a Lu\u0301cia saíram", "a [PESSOA] e a [PESSOA] saíram"],
      ["mora no bairro Jardim A\u0302ngela", "mora no [LOCAL]"],
      ["Rua Sa\u0303o Joa\u0303o, 12", "[LOCAL]"],
    ];
    for (const [text, output] of cases) {
      const result = anonymize(text);
      assert.equal(result.text, output, text);
      checkInvariants(text, result);
    }
  });

  it("gives the composed answer for every shared case written in NFD", () => {
    for (const c of CASES.must_mask.filter((x) => !x.python_only)) {
      const nfd = c.text.normalize("NFD");
      const result = anonymize(nfd);
      checkInvariants(nfd, result);
      assert.equal(result.text.normalize("NFC"), anonymize(c.text).text.normalize("NFC"), c.text);
    }
  });
});

describe("runtime support", () => {
  it("needs no RegExp modifiers (V8 12.5), so Node 22 loads it", () => {
    const dir = new URL("../anonymize/", import.meta.url);
    for (const file of readdirSync(dir).filter((f) => f.endsWith(".js"))) {
      assert.ok(!/\(\?[ims-]+:/.test(readFileSync(new URL(file, dir), "utf8")), file);
    }
  });

  it("keeps the street and neighborhood cues case-insensitive", () => {
    assert.deepEqual(masked("AVENIDA Paulista, 1000"), [["LOCAL", "AVENIDA Paulista, 1000"]]);
    assert.deepEqual(masked("PRAÇA da Sé, 10"), [["LOCAL", "PRAÇA da Sé, 10"]]);
    assert.deepEqual(masked("BAIRRO Jardim Ângela"), [["LOCAL", "BAIRRO Jardim Ângela"]]);
    assert.deepEqual(masked("rua sem número"), []);
  });
});

describe("regex safety", () => {
  const hostile = [
    "a".repeat(5000),
    "1".repeat(5000),
    "a.".repeat(2500),
    "@".repeat(2500) + "a",
    "a@".repeat(2500),
    "Rua ".repeat(1200) + "A",
    "cpf é é é é ".repeat(400) + "x",
    "1.1.1.1.".repeat(600),
    "Aa ".repeat(1600),
    "o gerente ".repeat(500) + "Antunes",
    "@a ".repeat(1700),
    "São ".repeat(1500),
    "moro em ".repeat(800) + "pirituba",
    "São José dos ".repeat(500),
  ];
  for (const text of hostile) {
    it(`handles ${label(text.slice(0, 20))} x${text.length} quickly`, () => {
      const start = performance.now();
      checkInvariants(text, anonymize(text));
      assert.ok(performance.now() - start < 250, "took too long");
    });
  }
});

describe("CPU budget", () => {
  // The Free plan allows 10 ms of CPU per request, so the work must grow linearly with the text:
  // a text 16 times longer may take at most 40 times as long (a quadratic step takes far more).
  const best = (text) => {
    anonymize(text);
    return Math.min(...[0, 1, 2].map(() => {
      const start = performance.now();
      anonymize(text);
      return performance.now() - start;
    }));
  };
  for (const unit of ["@a ", "Paulo ", "12345678 ", "Rua A, 1 ", "Paulo, a@b.co ", "moro em Guarulhos ", "São José dos Campos "]) {
    it(`grows linearly on ${label(unit)}`, () => {
      const small = best(unit.repeat(Math.ceil(2000 / unit.length)));
      const large = best(unit.repeat(Math.ceil(32000 / unit.length)));
      assert.ok(large < 40 * small + 5, `2 KB: ${small.toFixed(2)} ms, 32 KB: ${large.toFixed(2)} ms`);
    });
  }
});
