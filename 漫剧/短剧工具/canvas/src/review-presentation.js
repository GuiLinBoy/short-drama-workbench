// Presentation only. Never changes packets, fingerprints or approval rules.
export const REVIEW_FOCUS = {
  plan: ['剧情与台词是否满意', '风格、时长与画幅是否合适', '预算金额、次数和返工范围是否接受'],
  storyboard: ['镜头内容与叙事顺序是否清楚', '人物、场景与动作是否符合剧本'],
  image_prompt: ['提示词中的人物、场景、风格和构图是否符合想法', '参考素材是否选对'],
  image: ['长相、年龄感、服装与画面风格是否满意', '是否接受下方说明中的效果偏差'],
  audio: ['声音、语气、语速与台词是否合适', '是否采用这一版声音'],
  video_prompt: ['实际提示词中的动作、对白和镜头是否正确', '参考图、声音与预期效果是否一致'],
  video: ['人物、动作、表情与镜头效果是否满意', '对白、口型和衔接是否自然'],
  final: ['完整观看：故事、节奏、画面和声音是否满意', '字幕是否准确，是否同意交付这一版成片'],
  exception: ['这次申请改变什么、为什么需要', '新增费用、次数与执行范围是否接受'],
};

export function reviewPresentation(item, items = []) {
  const files = item.files || [];
  const prompts = [];
  const draftPrompt = item.draft ? files.find(file => file.path === item.draft.prompt) : null;
  if (item.request) prompts.push(item);
  // Only show the corresponding direct prompt, not an unrelated ancestor's
  // current request. Keep its current review status visible beside the text.
  if (['image', 'audio', 'video'].includes(item.kind)) {
    const expected = item.kind === 'video' ? 'video_prompt' : 'image_prompt';
    for (const id of item.dependsOn || []) {
      const dep = items.find(v => v.id === id);
      if (dep?.kind === expected && dep.request) prompts.push(dep);
    }
  }
  const primary = [], supporting = [], media = [];
  for (const file of files) {
    if (file === draftPrompt) continue;
    if (!('text' in file)) {
      if (item.status !== 'changes_requested' && file.path !== item.editSource?.path) media.push(file);
      continue;
    }
    const name = `${file.label || ''} ${file.path || ''}`;
    const technical = /证据|回执|日志|检查|核验|执行|交付说明|版本说明/i.test(name);
    const creative = /剧本|节拍|提示词|预算提案|预算方案|分镜|故事梗概/.test(name);
    (creative && !technical && !item.draft && !(prompts.length && /提示词/.test(name)) ? primary : supporting).push(file);
  }
  const declared = item.draft?.references || [];
  const references = declared.map(ref => ({...ref, file: media.find(file => file.path === ref.path)}));
  const included = new Set(declared.map(ref => ref.path).filter(Boolean));
  references.push(...media.filter(file => !included.has(file.path)).map(file => ({id:file.path,
    kind:file.resource?.mime?.startsWith('audio/')?'audio':file.resource?.mime?.startsWith('video/')?'video':'image',
    label:file.label || '生成参考素材', status:'available', file})));
  const focus = item.draft ? ['草稿中的动作、对白、镜头和节奏是否符合想法', '尾帧、声音及前后状态的依赖是否清楚'] : REVIEW_FOCUS[item.kind] || ['内容和预期效果是否满意'];
  return {focus, prompts, draftPrompt, references, media, primary, supporting};
}
