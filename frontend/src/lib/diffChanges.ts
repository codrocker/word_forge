export type Change = {
  field_path: string;
  target_id: number | null;
  op: 'update';
  old_value: unknown;
  new_value: unknown;
};

export type WordDetailForm = {
  word: {
    word_id: number;
    form: string;
    phonetic_us: string;
    phonetic_uk: string;
    status: number;
    quality_flag: string;
    type: number;
    audio_us: string | null;
    audio_uk: string | null;
  };
  meanings: {
    meaning_id: number;
    cn_paraphrase: string;
    en_paraphrase: string;
    pos: number;
  }[];
  mnemonics: {
    mnemonic_id: number;
    content: unknown; // JSONB — read-only in MVP
  }[];
  sentences: {
    sentence_id: number;
    form: string;
    translation: string;
  }[];
  phrases: {
    phrase_id: number;
    form: string;
    meaning: string; // actual column (not 'translation')
  }[];
};

/* eslint-disable @typescript-eslint/no-explicit-any */
export function diffChanges(
  defaults: WordDetailForm,
  current: WordDetailForm,
  dirtyFields: any,
): Change[] {
  const changes: Change[] = [];

  // words.*
  if (dirtyFields.word?.form) {
    changes.push({
      field_path: 'words.form',
      target_id: null,
      op: 'update',
      old_value: defaults.word.form,
      new_value: current.word.form,
    });
  }
  if (dirtyFields.word?.phonetic_us) {
    changes.push({
      field_path: 'words.phonetic_us',
      target_id: null,
      op: 'update',
      old_value: defaults.word.phonetic_us,
      new_value: current.word.phonetic_us,
    });
  }
  if (dirtyFields.word?.phonetic_uk) {
    changes.push({
      field_path: 'words.phonetic_uk',
      target_id: null,
      op: 'update',
      old_value: defaults.word.phonetic_uk,
      new_value: current.word.phonetic_uk,
    });
  }

  // meanings[i].cn_paraphrase / en_paraphrase
  (dirtyFields.meanings ?? []).forEach((dm: any, i: number) => {
    if (!dm) return;
    const m = current.meanings[i];
    const d = defaults.meanings[i];
    if (!m || !d) return;
    if (dm.cn_paraphrase) {
      changes.push({
        field_path: 'meanings.cn_paraphrase',
        target_id: m.meaning_id,
        op: 'update',
        old_value: d.cn_paraphrase,
        new_value: m.cn_paraphrase,
      });
    }
    if (dm.en_paraphrase) {
      changes.push({
        field_path: 'meanings.en_paraphrase',
        target_id: m.meaning_id,
        op: 'update',
        old_value: d.en_paraphrase,
        new_value: m.en_paraphrase,
      });
    }
  });

  // mnemonics.content is JSONB — MVP keeps it read-only, no diff emitted

  // sentences[i].form / translation
  (dirtyFields.sentences ?? []).forEach((dm: any, i: number) => {
    if (!dm) return;
    const m = current.sentences[i];
    const d = defaults.sentences[i];
    if (!m || !d) return;
    if (dm.form) {
      changes.push({
        field_path: 'sentences.form',
        target_id: m.sentence_id,
        op: 'update',
        old_value: d.form,
        new_value: m.form,
      });
    }
    if (dm.translation) {
      changes.push({
        field_path: 'sentences.translation',
        target_id: m.sentence_id,
        op: 'update',
        old_value: d.translation,
        new_value: m.translation,
      });
    }
  });

  // phrases[i].form / translation
  (dirtyFields.phrases ?? []).forEach((dm: any, i: number) => {
    if (!dm) return;
    const m = current.phrases[i];
    const d = defaults.phrases[i];
    if (!m || !d) return;
    if (dm.form) {
      changes.push({
        field_path: 'phrases.form',
        target_id: m.phrase_id,
        op: 'update',
        old_value: d.form,
        new_value: m.form,
      });
    }
    if (dm.meaning) {
      changes.push({
        field_path: 'phrases.meaning',
        target_id: m.phrase_id,
        op: 'update',
        old_value: d.meaning,
        new_value: m.meaning,
      });
    }
  });

  return changes;
}
