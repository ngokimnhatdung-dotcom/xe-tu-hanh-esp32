"""Xe ESP32 tranh vat can - MicroPython / Thonny.
Ban: quet tung buoc, chi quay phai, nguong dung 30 cm.

DAU DAY (nhin trai/phai theo huong tien cua xe):
  L298N IN1=GPIO27, IN2=GPIO26 -> motor trai OUT1/OUT2.
  L298N IN3=GPIO25, IN4=GPIO33 -> motor phai OUT3/OUT4.
  Giu jumper ENA/ENB: PWM dieu khien tren IN1..IN4.
  HC-SR04 TRIG=GPIO5; ECHO -> TXS B1 -> A1 -> GPIO18.
  Servo SG90 tin hieu=GPIO13; nguon servo=5V.
  TXS VA/OE=3V3, VB=5V; tat ca chung GND.

HOAT DONG:
  Khoi dong 3 giay, dung quet truoc khi di.
  Servo quay moi buoc 15 do, dung 70 ms, do DUNG MOT LAN.
  Pham vi servo 30..150 do; luon di tung buoc, ke ca luc ve giua.
  Trong luc chay, servo nhin thang; khoang cach <=30 cm thi dung.
  Khi dung: quet; neu vung truoc chua thoang, chi quay PHAI mot lan.
  Sau MOI cu quay: dung va quet lai, it nhat 1 giay moi duoc di tiep.
  Chi tien khi cac diem quet trong vung 75..105 do deu >30 cm.
  Khong so sanh trai/phai de chon huong quay.

CAN CHINH:
  TURN_45_MS=150 la uoc luong ban dau cho 45 do, khong phai goc do duoc.
  Khong co encoder/IMU, khong the bao dam tu dong dung 45 do.
  Hai motor dung PWM on dinh; LEFT_TRIM/RIGHT_TRIM de can thu cong.
  Cung duty PWM khong bao dam cung toc do banh.
  Xe lech trai: giam RIGHT_TRIM, vi du 1.00 -> 0.97.
  Xe lech phai: giam LEFT_TRIM, vi du 1.00 -> 0.97.
  Can hai banh truoc, sau do chinh TURN_45_MS moi lan 10..20 ms.
  Quay qua 45 do: giam thoi gian; chua du: tang thoi gian.
  Khi doi pin, mat san, tai trong/PWM, can kiem tra lai goc quay.

CHAY:
  Mo file trong Thonny, MicroPython (ESP32), F5. Ctrl+C de dung.
  Chi luu thanh main.py tren ESP32 sau khi thu dung chieu hai banh.
  Loi/timeout ECHO se dung xe va ket thuc; xem Shell, sua loi roi F5.
  Khi cam USB laptop, ngat day 5V ngoai vao VIN/5V ESP32; giu GND chung.
  Khi chay doc lap, cap 5V phu hop cho ESP32, khong cap pin 7.4V vao 5V/3V3.

Nguon API: https://docs.micropython.org/en/v1.29.0/library/machine.PWM.html
HC-SR04: https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf
"""

from machine import Pin, PWM, time_pulse_us
from time import sleep_ms, sleep_us, ticks_ms, ticks_diff

# ===== CHAN KET NOI =====
IN1_PIN, IN2_PIN, IN3_PIN, IN4_PIN = 27, 26, 25, 33
TRIG_PIN, ECHO_PIN, SERVO_PIN = 5, 18, 13

# Doi True neu lenh tien lam motor tuong ung quay lui.
INVERT_LEFT = False
INVERT_RIGHT = False

# ===== TOC DO VA CAN HAI BANH =====
FORWARD_PERCENT = 60
TURN_PERCENT = 50
LEFT_TRIM = 1.00
RIGHT_TRIM = 1.00
MOTOR_PWM_HZ = 1000
TURN_45_MS = 150          # Can thu tren xe de dat GAN 45 do.
REVERSE_PAUSE_MS = 150

# ===== CAM BIEN VA SERVO =====
STOP_DISTANCE_CM = 30
CENTER_ANGLE = 90
SCAN_MIN_ANGLE = 30
SCAN_MAX_ANGLE = 150
SCAN_STEP_DEG = 15       # Moi lan chi quay toi da 15 do, roi do mot lan.
FRONT_HALF_ANGLE = 15    # Vung truoc: 90-15 den 90+15 = 75..105 do.
SERVO_STEP_MS = 70
MIN_SCAN_MS = 1000       # Xe dung it nhat 1 giay trong moi luot quet.
SERVO_MIN_US = 500
SERVO_MAX_US = 2500
READ_INTERVAL_MS = 80
PING_INTERVAL_MS = 65    # HC-SR04: cach hai lan kich hon 60 ms.
ECHO_TIMEOUT_US = 30000


def main():
    motor_pins = [Pin(n, Pin.OUT, value=0)
                  for n in (IN1_PIN, IN2_PIN, IN3_PIN, IN4_PIN)]
    motor_pwms = []
    servo_pwm = None
    current_motion = (0, 0)
    current_angle = CENTER_ANGLE
    last_ping_ms = None

    def stop():
        nonlocal current_motion
        for output in motor_pwms:
            output.duty_u16(0)
        current_motion = (0, 0)

    def set_motors(left_percent, right_percent):
        nonlocal current_motion
        left = max(-100, min(100, int(round(left_percent * LEFT_TRIM))))
        right = max(-100, min(100, int(round(right_percent * RIGHT_TRIM))))
        if INVERT_LEFT:
            left = -left
        if INVERT_RIGHT:
            right = -right
        target = (left, right)
        if target == current_motion:
            return
        if any(old * new < 0 for old, new in zip(current_motion, target)):
            stop()
            sleep_ms(REVERSE_PAUSE_MS)

        # Dua ca hai ngo vao moi cau ve 0 truoc khi doi chieu.
        for output in motor_pwms:
            output.duty_u16(0)
        if left:
            motor_pwms[0 if left > 0 else 1].duty_u16(abs(left) * 65535 // 100)
        if right:
            motor_pwms[2 if right > 0 else 3].duty_u16(abs(right) * 65535 // 100)
        current_motion = target

    def servo_to(angle):
        nonlocal current_angle
        current_angle = int(angle)
        pulse_us = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * current_angle // 180
        servo_pwm.duty_ns(pulse_us * 1000)

    def distance_cm():
        nonlocal last_ping_ms
        if last_ping_ms is not None:
            elapsed = ticks_diff(ticks_ms(), last_ping_ms)
            if elapsed < PING_INTERVAL_MS:
                sleep_ms(PING_INTERVAL_MS - elapsed)
        if echo.value():
            raise RuntimeError("ECHO dang HIGH; kiem tra cam bien, TXS va day noi")
        trigger.value(0)
        sleep_us(2)
        last_ping_ms = ticks_ms()
        trigger.value(1)
        sleep_us(10)
        trigger.value(0)
        pulse = time_pulse_us(echo, 1, ECHO_TIMEOUT_US)
        if pulse <= 0:
            raise RuntimeError("Khong nhan ECHO tai goc %d do" % current_angle)
        return pulse * 0.034 / 2

    def measure_at_current_angle(readings):
        # Moi vi tri vua toi chi phat mot xung TRIG, khong lay nhieu mau.
        cm = distance_cm()
        readings[current_angle] = cm
        print("Goc %3d: %6.1f cm" % (current_angle, cm))

    def scan_to(target, readings):
        # Ca duong di va duong ve deu di tung buoc, moi buoc do mot lan.
        while current_angle != target:
            step = min(SCAN_STEP_DEG, abs(target - current_angle))
            next_angle = current_angle + (step if target > current_angle else -step)
            servo_to(next_angle)
            sleep_ms(SERVO_STEP_MS)
            measure_at_current_angle(readings)

    def scan_surroundings():
        stop()
        started = ticks_ms()
        readings = {}
        print("DUNG XE - QUET TUNG BUOC")
        sleep_ms(SERVO_STEP_MS)
        measure_at_current_angle(readings)
        scan_to(SCAN_MAX_ANGLE, readings)
        scan_to(SCAN_MIN_ANGLE, readings)

        # Lam moi vung truoc cuoi luot quet: 75, 90, 105, roi ve 90.
        # Khong de so do thang cu tu dau luot quet cho phep xe tien.
        scan_to(CENTER_ANGLE + FRONT_HALF_ANGLE, readings)
        remaining = MIN_SCAN_MS - ticks_diff(ticks_ms(), started)
        if remaining > 0:
            sleep_ms(remaining)
        scan_to(CENTER_ANGLE, readings)

        forward_distances = [
            cm for angle, cm in readings.items()
            if abs(angle - CENTER_ANGLE) <= FRONT_HALF_ANGLE
        ]
        nearest = min(forward_distances)
        print("Gan nhat trong vung truoc: %.1f cm" % nearest)
        return nearest > STOP_DISTANCE_CM

    def turn_right_45():
        stop()
        print("QUAY PHAI - muc tieu 45 do (%d ms)" % TURN_45_MS)
        # Nhin tu tren: banh trai tien, banh phai lui -> quay phai.
        set_motors(TURN_PERCENT, -TURN_PERCENT)
        sleep_ms(TURN_45_MS)
        stop()
        # Vong lap bat buoc goi scan_surroundings() ngay sau day.

    try:
        if not (0 < SCAN_STEP_DEG <= FRONT_HALF_ANGLE):
            raise ValueError("SCAN_STEP_DEG phai >0 va <= FRONT_HALF_ANGLE")
        if not (0 <= SCAN_MIN_ANGLE < CENTER_ANGLE - FRONT_HALF_ANGLE
                < CENTER_ANGLE < CENTER_ANGLE + FRONT_HALF_ANGLE
                < SCAN_MAX_ANGLE <= 180):
            raise ValueError("Kiem tra goc servo va pham vi quet")
        if not (0 < FORWARD_PERCENT <= 100 and 0 < TURN_PERCENT <= 100
                and LEFT_TRIM > 0 and RIGHT_TRIM > 0
                and TURN_45_MS > 0 and STOP_DISTANCE_CM > 0):
            raise ValueError("Kiem tra toc do, he so can banh va thoi gian quay")
        if SERVO_STEP_MS < 65 or MIN_SCAN_MS < 1000:
            raise ValueError("SERVO_STEP_MS >=65; MIN_SCAN_MS >=1000")

        for pin in motor_pins:
            motor_pwms.append(PWM(pin, freq=MOTOR_PWM_HZ, duty_u16=0))
        trigger = Pin(TRIG_PIN, Pin.OUT, value=0)
        echo = Pin(ECHO_PIN, Pin.IN, pull=None)
        servo_pwm = PWM(Pin(SERVO_PIN), freq=50, duty_u16=0)
        # Vi tri servo luc vua cap nguon chua biet: dat ve giua mot lan.
        servo_to(CENTER_ANGLE)
        print("Xe tu hanh: dung 30 cm, chi quay phai. Ctrl+C de dung.")
        for seconds in (3, 2, 1):
            print("Bat dau sau", seconds, "giay...")
            sleep_ms(1000)

        need_scan = True
        while True:
            if need_scan:
                clear = scan_surroundings()
                while not clear:
                    turn_right_45()
                    clear = scan_surroundings()
                need_scan = False
                print("Vung truoc thoang - TIEN")

            # Servo o giua trong khi di; PWM giu on dinh giua cac lan do.
            set_motors(FORWARD_PERCENT, FORWARD_PERCENT)
            sleep_ms(READ_INTERVAL_MS)
            front = distance_cm()
            if front <= STOP_DISTANCE_CM:
                stop()
                print("VAT CAN %.1f cm - DUNG" % front)
                need_scan = True

    except KeyboardInterrupt:
        stop()
        print("Da dung theo yeu cau.")
    except Exception as exc:
        stop()
        print("LOI:", exc)
        print("Xe da dung. Kiem tra day/nguon/cam bien roi nhan F5 lai.")
    finally:
        stop()
        for output in motor_pwms:
            output.deinit()
        for pin in motor_pins:
            pin.init(Pin.OUT, value=0)
        if servo_pwm is not None:
            servo_pwm.deinit()
        print("Da tat tin hieu dieu khien hai motor.")


if __name__ == "__main__":
    main()
