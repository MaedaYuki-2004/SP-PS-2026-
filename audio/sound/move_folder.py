import shutil

def move_file(num):
    for i in range(num):
        path1 = './Native_50/word%d.wav' % (i+1)
        path2 = '../wab_main7/audio/sound/word%d' % i
        path = shutil.move(path1, path2)
        print(path)

num = 30
move_file(num)