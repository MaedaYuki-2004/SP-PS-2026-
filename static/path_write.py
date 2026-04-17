import os

file_path = 'audio.scp'

with open(file_path, mode='w', encoding='utf-8') as f:
    for i in range(1,31):
        audio_path = './audio/sound/word%d/word%d.wav' % (i,i)
        f.write(audio_path+'\n')