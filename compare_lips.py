import cv2
import mediapipe as mp
import math
import numpy as np
from fastdtw import fastdtw
from scipy.spatial.distance import euclidean

# MediaPipeの準備
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

def calc_distance(p1, p2, w, h):
    x1, y1 = int(p1.x * w), int(p1.y * h)
    x2, y2 = int(p2.x * w), int(p2.y * h)
    return math.sqrt((x2 - x1)**2 + (y2 - y1)**2)

# ==========================================
# ★ 調整可能なパラメータ
# ==========================================
max_frames = 50
# 唇の輪郭を細かく捉える20個の点（MediaPipeの公式番号）
LIP_POINTS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 185, 40, 39, 37, 0, 267, 269, 270, 409]
# ==========================================

reference_vectors = [] # 点数計算用の8個のデータ（お手本）
reference_ratios = []  # グラフ描画用の口の開き具合（お手本）
test_vectors = []      # 点数計算用の8個のデータ（テスト）
test_ratios = []       # グラフ描画用の口の開き具合（テスト）

recording_mode = None
recorded_frames = 0
last_score = None

# 口の形データ（20個の数字）を計算する関数
def get_lip_shape_vector(landmarks, w, h):
    face_scale = calc_distance(landmarks.landmark[33], landmarks.landmark[263], w, h)
    if face_scale == 0: face_scale = 1
    
    top_inner = landmarks.landmark[13]
    bottom_inner = landmarks.landmark[14]
    center_x = (top_inner.x + bottom_inner.x) / 2 * w
    center_y = (top_inner.y + bottom_inner.y) / 2 * h
    
    shape_vector = []
    for idx in LIP_POINTS:
        pt = landmarks.landmark[idx]
        px, py = pt.x * w, pt.y * h
        dist_to_center = math.sqrt((px - center_x)**2 + (py - center_y)**2)
        shape_vector.append(dist_to_center / face_scale)
        
    return np.array(shape_vector)

# グラフの画像を作成する関数
def draw_graph(ref_ratios, test_ratios):
    # 幅400px、高さ200pxの黒い背景を作成
    graph_img = np.zeros((200, 400, 3), dtype=np.uint8)
    
    # グラフの枠と目盛り線を引く（装飾）
    cv2.line(graph_img, (0, 100), (400, 100), (50, 50, 50), 1)
    cv2.putText(graph_img, "Mouth Openness", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(graph_img, "White: Ref / Red: Test", (10, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    # データをグラフの座標に変換して線を引く
    step_x = 400 / max_frames
    
    # お手本のグラフ（白色）を描画
    for i in range(1, len(ref_ratios)):
        x1 = int((i - 1) * step_x)
        y1 = int(200 - (ref_ratios[i - 1] * 250)) # 250はグラフの高さを調整するための倍率
        x2 = int(i * step_x)
        y2 = int(200 - (ref_ratios[i] * 250))
        cv2.line(graph_img, (x1, y1), (x2, y2), (255, 255, 255), 2)
        
    # テストのグラフ（赤色）を描画
    if len(test_ratios) > 0:
        for i in range(1, len(test_ratios)):
            x1 = int((i - 1) * step_x)
            y1 = int(200 - (test_ratios[i - 1] * 250))
            x2 = int(i * step_x)
            y2 = int(200 - (test_ratios[i] * 250))
            cv2.line(graph_img, (x1, y1), (x2, y2), (0, 0, 255), 2)

    return graph_img

cap = cv2.VideoCapture(0)

print("========================================")
print("詳細な口の動き比較＆グラフ表示システム！")
print("『 r 』キー : お手本を録画")
print("『 t 』キー : テスト録画して比較")
print("『 q 』キー : 終了")
print("========================================")

show_graph = False

while cap.isOpened():
    success, image = cap.read()
    if not success: break

    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(image_rgb)
    h, w, _ = image.shape

    if results.multi_face_landmarks:
        for face_landmarks in results.multi_face_landmarks:
            # 20個の点を青色で描画
            for idx in LIP_POINTS:
                pt = face_landmarks.landmark[idx]
                cv2.circle(image, (int(pt.x * w), int(pt.y * h)), 2, (0, 255, 0), -1)

            if recording_mode is not None:
                # 形のデータ（20個）を取得
                shape_vector = get_lip_shape_vector(face_landmarks, w, h)
                
                # グラフ用に「縦の開き具合（縦幅÷横幅）」も計算して保存
                top_lip = face_landmarks.landmark[13]
                bottom_lip = face_landmarks.landmark[14]
                left_lip = face_landmarks.landmark[61]
                right_lip = face_landmarks.landmark[291]
                v_dist = calc_distance(top_lip, bottom_lip, w, h)
                h_dist = calc_distance(left_lip, right_lip, w, h)
                ratio = v_dist / (h_dist + 1e-6)

                if recording_mode == "ref":
                    reference_vectors.append(shape_vector)
                    reference_ratios.append(ratio)
                elif recording_mode == "test":
                    test_vectors.append(shape_vector)
                    test_ratios.append(ratio)

                recorded_frames += 1
                mode_text = "Reference" if recording_mode == "ref" else "Test"
                cv2.putText(image, f"Recording {mode_text}... {recorded_frames}/{max_frames}", 
                            (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

                if recorded_frames >= max_frames:
                    print(f"{mode_text} の録画が完了！")
                    
                    if recording_mode == "test" and len(reference_vectors) == max_frames:
                        # 20個の点のデータを使って距離を計算
                        distance, _ = fastdtw(reference_vectors, test_vectors, dist=euclidean)
                        
                        # 20点に増えたのでスコア計算の倍率をさらに調整
                        score = int(100 - (distance * 2)) # 距離が大きいほど減点、倍率は調整可能
                        last_score = max(0, min(100, score))
                        print(f"比較完了！ズレ: {distance:.2f}, スコア: {last_score}点")
                        
                        # グラフを表示するフラグをオンにする
                        show_graph = True
                    
                    recording_mode = None

    # 操作案内とスコアの表示
    if recording_mode is None:
        cv2.putText(image, "Press 'r': Record Ref", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(image, "Press 't': Test Movement", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        if last_score is not None:
            color = (0, 255, 0) if last_score > 80 else (0, 165, 255)
            cv2.putText(image, f"Score: {last_score}/100", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.5, color, 3)

    cv2.imshow('Lip Movement Comparator', image)

    # グラフの描画と表示
    if show_graph and len(reference_ratios) > 0 and len(test_ratios) > 0:
        graph_image = draw_graph(reference_ratios, test_ratios)
        cv2.imshow('Movement Graph', graph_image)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'): break
    elif key == ord('r') and recording_mode is None:
        recording_mode = "ref"
        recorded_frames = 0
        reference_vectors = []
        reference_ratios = []
        show_graph = False # 新しく録画するときはグラフを隠す
    elif key == ord('t') and recording_mode is None:
        if len(reference_vectors) == max_frames:
            recording_mode = "test"
            recorded_frames = 0
            test_vectors = []
            test_ratios = []
            show_graph = False
        else:
            print("先にお手本を録画してください！")

cap.release()
cv2.destroyAllWindows()