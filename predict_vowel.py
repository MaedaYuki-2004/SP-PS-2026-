import cv2
import mediapipe as mp
import math
import pickle
import warnings

# AIが警告を出すことがあるので、画面を見やすくするために非表示にする
warnings.filterwarnings("ignore", category=UserWarning)

# 1. AIモデルの読み込み
try:
    with open("vowel_model.pkl", "rb") as f:
        model = pickle.load(f)
except FileNotFoundError:
    print("エラー: 'vowel_model.pkl' が見つからないよ。先に train_vowel.py を実行してね！")
    exit()

# 2. MediaPipeの準備
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

# 距離計算の関数
def calc_distance(p1, p2, w, h):
    x1, y1 = int(p1.x * w), int(p1.y * h)
    x2, y2 = int(p2.x * w), int(p2.y * h)
    return math.sqrt((x2 - x1)**2 + (y2 - y1)**2)

# OpenCV（画面表示用）の文字化け対策の変換表
# 日本語のラベルを英語に変換する辞書
vowel_map = {
    "あ": "A",
    "い": "I",
    "う": "U",
    "え": "E",
    "お": "O",
    "無音": "Silent"
}

cap = cv2.VideoCapture(0)
print("リアルタイム判定をスタートするよ！終了するには 'q' を押してね。")

while cap.isOpened():
    success, image = cap.read()
    if not success:
        break

    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(image_rgb)
    
    h, w, _ = image.shape
    current_vowel = "Searching..."

    if results.multi_face_landmarks:
        for face_landmarks in results.multi_face_landmarks:
            top_lip = face_landmarks.landmark[13]
            bottom_lip = face_landmarks.landmark[14]
            left_lip = face_landmarks.landmark[61]
            right_lip = face_landmarks.landmark[291]

            # 唇のポイントを緑色で描画
            for pt in [top_lip, bottom_lip, left_lip, right_lip]:
                cv2.circle(image, (int(pt.x * w), int(pt.y * h)), 3, (0, 255, 0), -1)

            # 縦幅、横幅、比率を計算
            vertical_dist = calc_distance(top_lip, bottom_lip, w, h)
            horizontal_dist = calc_distance(left_lip, right_lip, w, h)
            ratio = vertical_dist / (horizontal_dist + 1e-6)
            
            # AIに判定させるためのデータ形式にまとめる
            input_data = [[vertical_dist, horizontal_dist, ratio]]
            
            # AIによる予測を実行
            prediction = model.predict(input_data)
            # 予測結果（日本語）を取り出す
            predicted_label = prediction[0]
            
            # 画面表示用に英語に変換（辞書にない場合はそのまま）
            current_vowel = vowel_map.get(predicted_label, predicted_label)

    # 判定結果を画面の左上に大きく表示
    cv2.putText(image, f"Vowel: {current_vowel}", (20, 50), 
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 0, 0), 3)
    
    cv2.imshow('Vowel Prediction', image)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()