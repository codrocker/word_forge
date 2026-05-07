import { type ReactNode } from 'react';
import { useForm, useFieldArray } from 'react-hook-form';
import { AppFormInput } from '@/components/app/AppFormInput';
import { AppButton } from '@/components/app/AppButton';
import { diffChanges, type WordDetailForm, type Change } from '@/lib/diffChanges';

type WordEditFormProps = {
  defaults: WordDetailForm;
  onSubmitChanges: (changes: Change[]) => void;
  children?: ReactNode;
};

export function WordEditForm({
  defaults,
  onSubmitChanges,
  children,
}: WordEditFormProps) {
  const { register, control, handleSubmit, formState } =
    useForm<WordDetailForm>({
      defaultValues: defaults,
    });

  const { fields: meaningFields } = useFieldArray({
    control,
    name: 'meanings',
    keyName: '_key',
  });
  const { fields: mnemonicFields } = useFieldArray({
    control,
    name: 'mnemonics',
    keyName: '_key',
  });
  const { fields: sentenceFields } = useFieldArray({
    control,
    name: 'sentences',
    keyName: '_key',
  });
  const { fields: phraseFields } = useFieldArray({
    control,
    name: 'phrases',
    keyName: '_key',
  });

  const onSubmit = (values: WordDetailForm) => {
    const changes = diffChanges(defaults, values, formState.dirtyFields);
    onSubmitChanges(changes);
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
      {/* Word header */}
      <section className="rounded-lg border bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-lg font-semibold text-gray-800">Word</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <AppFormInput label="Form" {...register('word.form')} />
          <AppFormInput
            label="Phonetic (US)"
            {...register('word.phonetic_us')}
          />
          <AppFormInput
            label="Phonetic (UK)"
            {...register('word.phonetic_uk')}
          />
        </div>
        {/* Read-only fields */}
        <div className="mt-3 flex flex-wrap gap-4 text-sm text-gray-500">
          {defaults.word.audio_us && (
            <span>Audio US: {defaults.word.audio_us}</span>
          )}
          {defaults.word.audio_uk && (
            <span>Audio UK: {defaults.word.audio_uk}</span>
          )}
          <span>Type: {defaults.word.type}</span>
        </div>
      </section>

      {/* Meanings */}
      {meaningFields.length > 0 && (
        <section className="rounded-lg border bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-lg font-semibold text-gray-800">
            Meanings
          </h2>
          <div className="space-y-3">
            {meaningFields.map((field, i) => (
              <div
                key={field._key}
                className="rounded border border-gray-100 bg-gray-50 p-3"
              >
                <span className="mb-2 inline-block text-xs text-gray-400">
                  #{field.meaning_id} &middot; pos={field.pos}
                </span>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <AppFormInput
                    label="CN Paraphrase"
                    {...register(`meanings.${i}.cn_paraphrase`)}
                  />
                  <AppFormInput
                    label="EN Paraphrase"
                    {...register(`meanings.${i}.en_paraphrase`)}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Mnemonics */}
      {mnemonicFields.length > 0 && (
        <section className="rounded-lg border bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-lg font-semibold text-gray-800">
            Mnemonics
          </h2>
          <div className="space-y-3">
            {mnemonicFields.map((field, i) => (
              <div
                key={field._key}
                className="rounded border border-gray-100 bg-gray-50 p-3"
              >
                <span className="mb-2 inline-block text-xs text-gray-400">
                  #{field.mnemonic_id}
                </span>
                <AppFormInput
                  label="Content"
                  {...register(`mnemonics.${i}.content`)}
                />
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Sentences */}
      {sentenceFields.length > 0 && (
        <section className="rounded-lg border bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-lg font-semibold text-gray-800">
            Sentences
          </h2>
          <div className="space-y-3">
            {sentenceFields.map((field, i) => (
              <div
                key={field._key}
                className="rounded border border-gray-100 bg-gray-50 p-3"
              >
                <span className="mb-2 inline-block text-xs text-gray-400">
                  #{field.sentence_id}
                </span>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <AppFormInput
                    label="Sentence"
                    {...register(`sentences.${i}.form`)}
                  />
                  <AppFormInput
                    label="Translation"
                    {...register(`sentences.${i}.translation`)}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Phrases */}
      {phraseFields.length > 0 && (
        <section className="rounded-lg border bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-lg font-semibold text-gray-800">
            Phrases
          </h2>
          <div className="space-y-3">
            {phraseFields.map((field, i) => (
              <div
                key={field._key}
                className="rounded border border-gray-100 bg-gray-50 p-3"
              >
                <span className="mb-2 inline-block text-xs text-gray-400">
                  #{field.phrase_id}
                </span>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <AppFormInput
                    label="Phrase"
                    {...register(`phrases.${i}.form`)}
                  />
                  <AppFormInput
                    label="Translation"
                    {...register(`phrases.${i}.translation`)}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Submit button */}
      <div className="flex items-center gap-4">
        <AppButton type="submit" disabled={!formState.isDirty}>
          Review Changes
        </AppButton>
        {!formState.isDirty && (
          <span className="text-sm text-gray-400">No changes to submit</span>
        )}
      </div>

      {/* Slot for DiffPreviewModal and StatusQualityToggle */}
      {children}
    </form>
  );
}
