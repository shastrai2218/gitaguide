export function generateShareCardCanvas({ replyText, verse, emotionLabel }) {
  const canvas = document.createElement('canvas')
  canvas.width = 1080
  canvas.height = 1350
  const ctx = canvas.getContext('2d')
  if (!ctx) return null

  // Background Parchment Gradient
  const grad = ctx.createLinearGradient(0, 0, 0, canvas.height)
  grad.addColorStop(0, '#FAF7F0')
  grad.addColorStop(1, '#F4EFE6')
  ctx.fillStyle = grad
  ctx.fillRect(0, 0, canvas.width, canvas.height)

  // Decorative border
  ctx.strokeStyle = '#B08D57'
  ctx.lineWidth = 4
  ctx.strokeRect(40, 40, canvas.width - 80, canvas.height - 80)

  ctx.strokeStyle = '#E1D9C5'
  ctx.lineWidth = 1.5
  ctx.strokeRect(55, 55, canvas.width - 110, canvas.height - 110)

  // Top Diya / Brand
  ctx.fillStyle = '#B08D57'
  ctx.font = '600 32px Georgia, serif'
  ctx.textAlign = 'center'
  ctx.fillText('G I T A G U I D E', canvas.width / 2, 120)

  ctx.fillStyle = '#7C9885'
  ctx.font = '500 20px -apple-system, sans-serif'
  ctx.letterSpacing = '2px'
  ctx.fillText('WISDOM GROUNDED GUIDANCE', canvas.width / 2, 155)

  // Divider
  ctx.strokeStyle = '#E1D9C5'
  ctx.beginPath()
  ctx.moveTo(canvas.width / 2 - 120, 185)
  ctx.lineTo(canvas.width / 2 + 120, 185)
  ctx.stroke()

  let currentY = 240

  // If verse exists, show Sanskrit & citation
  if (verse) {
    ctx.fillStyle = '#B08D57'
    ctx.font = 'bold 36px Georgia, serif'
    ctx.textAlign = 'center'
    const heading = verse.label || verse.ref || verse.id || 'Bhagavad Gita'
    ctx.fillText(heading, canvas.width / 2, currentY)
    currentY += 45

    if (verse.text_sanskrit) {
      ctx.fillStyle = '#4F6B62'
      ctx.font = 'italic 28px Georgia, serif'
      const sanskritLines = wrapText(ctx, verse.text_sanskrit, 880)
      for (const line of sanskritLines.slice(0, 3)) {
        ctx.fillText(line, canvas.width / 2, currentY)
        currentY += 38
      }
      currentY += 15
    }

    if (verse.text) {
      ctx.fillStyle = '#2B3242'
      ctx.font = '30px Georgia, serif'
      const verseLines = wrapText(ctx, `“${verse.text}”`, 860)
      for (const line of verseLines.slice(0, 4)) {
        ctx.fillText(line, canvas.width / 2, currentY)
        currentY += 42
      }
      currentY += 30
    }
  }

  // Key Guidance excerpt
  if (replyText) {
    ctx.strokeStyle = '#B08D57'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(canvas.width / 2 - 80, currentY)
    ctx.lineTo(canvas.width / 2 + 80, currentY)
    ctx.stroke()
    currentY += 45

    ctx.fillStyle = '#2B3242'
    ctx.font = '28px Georgia, serif'
    ctx.textAlign = 'center'

    // Clean markdown stars
    const cleanText = replyText
      .replace(/\*\*/g, '')
      .replace(/#+/g, '')
      .trim()
    const summaryLines = wrapText(ctx, cleanText, 860)

    for (const line of summaryLines.slice(0, 6)) {
      ctx.fillText(line, canvas.width / 2, currentY)
      currentY += 42
    }
  }

  // Bottom Footer
  ctx.fillStyle = '#6B6F68'
  ctx.font = '22px -apple-system, sans-serif'
  ctx.textAlign = 'center'
  ctx.fillText('Reflection & Guidance · gitaguide.ai', canvas.width / 2, canvas.height - 80)

  return canvas
}

function wrapText(ctx, text, maxWidth) {
  const words = text.split(/\s+/)
  const lines = []
  let currentLine = words[0] || ''

  for (let i = 1; i < words.length; i++) {
    const word = words[i]
    const width = ctx.measureText(currentLine + ' ' + word).width
    if (width < maxWidth) {
      currentLine += ' ' + word
    } else {
      lines.push(currentLine)
      currentLine = word
    }
  }
  if (currentLine) lines.push(currentLine)
  return lines
}

export function downloadShareCard({ replyText, verse, emotionLabel }) {
  const canvas = generateShareCardCanvas({ replyText, verse, emotionLabel })
  if (!canvas) return
  const link = document.createElement('a')
  link.download = `GitaGuide-${(verse?.id || 'guidance').replace(/[^a-zA-Z0-9-]/g, '_')}.png`
  link.href = canvas.toDataURL('image/png')
  link.click()
}
