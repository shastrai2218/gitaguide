/**
 * Practical reflections & micro-practices tailored to emotional states and dilemmas.
 */

export const PRACTICE_CATALOG = {
  frustration: {
    title: 'Mindful Pause & Release',
    quote: 'Detaching effort from immediate reward restores your agency.',
    steps: [
      'Take 3 deep, grounding breaths. Inhale for 4 counts, exhale slowly for 6.',
      'Acknowledge what was outside your control today—write it down or name it mentally, then consciously let it rest.',
      'Identify 1 small, productive action you can take this evening that belongs purely to your own self-growth.',
    ],
    duration: '2-3 mins',
  },
  anger: {
    title: 'The Cool Observer Reframe',
    quote: 'From anger arises confusion of mind; from confusion, loss of reason (BG 2.63).',
    steps: [
      'Step back physically or pause responding for at least 10 minutes.',
      'Drink a glass of water slowly while observing the physical tension in your shoulders.',
      'Ask yourself: "Will my immediate reaction improve this situation or give away my inner peace?"',
    ],
    duration: '3 mins',
  },
  sadness: {
    title: 'Self-Compassion & Gentle Grounding',
    quote: 'The soul is neither broken by grief nor dried by sorrow.',
    steps: [
      'Place a hand gently over your chest and acknowledge: "This hurts, and it is natural to feel this way right now."',
      'Step outside into fresh air or wash your face with cool water to reset your senses.',
      'Reach out to one supportive person or write a few lines of honest, unedited thought in a journal.',
    ],
    duration: '3 mins',
  },
  anxiety: {
    title: 'The 5-4-3-2-1 Sensory Grounding',
    quote: 'When the mind runs after wandering senses, it carries away understanding like wind on water (BG 2.67).',
    steps: [
      'Notice 5 things you can see around you right now.',
      'Notice 4 things you can physically feel (the chair beneath you, the cool air).',
      'Listen for 3 distinct sounds in your surroundings.',
      'Notice 2 scents or textures, and take 1 deep breath into the present moment.',
    ],
    duration: '2 mins',
  },
  fear: {
    title: 'Courage of Duty (Svadharma)',
    quote: 'Perform your prescribed duty with courage, free from attachment.',
    steps: [
      'Separate the imagined worst-case future from the concrete reality of this exact minute.',
      'Write down: "What is the single next right step I can take right now?"',
      'Commit to taking only that step, trusting that future moments will be met as they arrive.',
    ],
    duration: '2 mins',
  },
  confusion: {
    title: 'Clarity Journaling',
    quote: 'Reason steps in when written formulas conflict with reality.',
    steps: [
      'Draw two columns on a piece of paper: "What I control" and "What I cannot control".',
      'Sort your current dilemma into these two columns without judging yourself.',
      'Focus 100% of your energy on the first column.',
    ],
    duration: '4 mins',
  },
  default: {
    title: 'Equanimity (Samatvam) Reflection',
    quote: 'Equanimity of mind is called Yoga (BG 2.48).',
    steps: [
      'Close your eyes for 60 seconds and release the need to fix everything right now.',
      'Remind yourself: You have a right to your noble efforts, but the fruits belong to the greater universe.',
      'Anchor yourself in patience and quiet confidence.',
    ],
    duration: '1-2 mins',
  },
}

export function getPracticeForEmotion(emotionLabel) {
  const norm = (emotionLabel || '').toLowerCase().trim()
  for (const [key, val] of Object.entries(PRACTICE_CATALOG)) {
    if (norm.includes(key)) return val
  }
  return PRACTICE_CATALOG.default
}

