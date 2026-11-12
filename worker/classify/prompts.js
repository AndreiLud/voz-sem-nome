// Prompts of the Worker classifier: port of user_message() in src/enps/classify/prompts.py.
// The system prompts themselves are rendered by the Python code into data.js, with the same
// prompt_version strings, so a classification record says exactly which text ran.

import { PROMPTS } from "./data.js";
import { strip } from "./text.js";

export { PROMPTS };

const QUOTE_RUN = /"{3,}/g;

/** The user turn: only the comment, plus the validation error on a retry. */
export function userMessage(text, errorHint = null) {
  const body = strip(text).replace(QUOTE_RUN, '"'); // the comment can never close the delimiter
  let message = `Comentário: """${body}"""`;
  if (errorHint) {
    message += `\n\nSua resposta anterior foi recusada: ${errorHint}. Responda de novo, só com o JSON, seguindo as regras.`;
  }
  return message;
}
