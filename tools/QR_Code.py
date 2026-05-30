import qrcode

# === 这里是要转换的网址，你可以直接修改这里 ===
# target_url = "https://kaimenlai.com/clashx/00174147-8a55-4392-9e6b-cfbf795d043c"
target_url = "https://kaimenlai.com/subscription/shadowrocket/00174147-8a55-4392-9e6b-cfbf795d043c"


# ==========================================

def generate_qr_code(url, file_name="qrcode.png"):
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        img.save(file_name)
        print(f"成功！二维码已保存为: {file_name}")
        print("程序运行结束。")

    except Exception as e:
        print(f"发生错误: {e}")


if __name__ == "__main__":
    # 直接调用函数，不需要 input 等待
    generate_qr_code(target_url, "my_website.png")